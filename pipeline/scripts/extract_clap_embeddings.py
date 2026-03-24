"""Runner: CLAP embedding extraction for all unprocessed recordings.

Loads one CLAP model in the main process, then processes recordings in parallel
using a thread pool. Each thread:
  1. Loads and decodes its recording's audio (I/O + CPU, concurrent)
  2. Acquires a shared lock to run batched CLAP inference (serialized per call)
  3. Returns embeddings; the main thread writes to SQLite and marks processed

Parallelism benefit: while one thread runs inference, others decode audio —
the bottleneck alternates between I/O and compute, keeping CPU busy throughout.

Usage:
  python -m pipeline.scripts.extract_clap_embeddings [options]

Options:
  --workers N          Thread count (default: 4)
  --batch-size N       Segments per CLAP forward pass (default: 16)
  --recording-id N     Process a single recording by ID
  --tier N [N ...]     Restrict to dev subset(s): --tier 1, --tier 2, --tier 1 2
  --dry-run            Print what would be processed without writing
"""

import argparse
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

load_dotenv()

from pipeline.config import PROCESSED_ROOT
from pipeline.db.models import Recording
from pipeline.db.processing import mark_processed, needs_processing
from pipeline.db.segments import ensure_segments
from pipeline.db.session import SessionLocal
from pipeline.features.clap_embeddings import compute_embeddings, load_model, write_embeddings
from pipeline.ingest.tiers import filter_recording_ids_by_tier, tier_paths_for

CLAP_VERSION = "1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract CLAP embeddings for recordings.")
    p.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Thread count (default: 4)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=16,
        help="Segments per CLAP forward pass (default: 16)",
    )
    p.add_argument(
        "--recording-id",
        type=int,
        default=None,
        help="Process a single recording by ID",
    )
    p.add_argument(
        "--tier",
        type=int,
        nargs="+",
        choices=[1, 2],
        metavar="N",
        default=None,
        help="Restrict to dev subset(s): --tier 1, --tier 2, or --tier 1 2",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be processed without writing to the DB",
    )
    return p.parse_args()


def _build_task(recording: Recording, segments: list) -> dict:
    return {
        "recording_id": recording.id,
        "audio_abs": str(PROCESSED_ROOT / recording.audio_path),
        "segments": [(s.id, s.start_seconds, s.end_seconds) for s in segments],
    }


def _is_eligible(recording: Recording | None, rec_id: int) -> bool:
    if recording is None:
        log.warning("Recording %d not found — skipping", rec_id)
        return False
    if recording.audio_path is None:
        log.warning("Recording %d has no audio_path — skipping", rec_id)
        return False
    if recording.duration_seconds is None:
        log.warning(
            "Recording %d has no duration_seconds — run backfill_durations.py first, skipping",
            rec_id,
        )
        return False
    return True


def main() -> None:
    args = _parse_args()
    db = SessionLocal()

    try:
        if args.recording_id is not None:
            recording_ids = [args.recording_id]
        else:
            recording_ids = needs_processing(db, "clap", CLAP_VERSION)

        if args.tier:
            tier_paths = tier_paths_for(args.tier)
            tier_label = "Tier " + "+".join(str(t) for t in sorted(args.tier))
            recording_ids = filter_recording_ids_by_tier(db, recording_ids, tier_paths)
            log.info("%s filter: %d recording(s) selected", tier_label, len(recording_ids))

        log.info("Recordings to process: %d", len(recording_ids))
        if not recording_ids:
            log.info("Nothing to do.")
            return

        if args.dry_run:
            for rec_id in recording_ids:
                rec = db.get(Recording, rec_id)
                if _is_eligible(rec, rec_id):
                    log.info("DRY  would process recording %d (%s)", rec_id, rec.audio_path)
            return

        log.info("Loading CLAP model…")
        model = load_model()
        model_lock = threading.Lock()
        log.info("CLAP model loaded. Using %d worker thread(s), batch_size=%d", args.workers, args.batch_size)

        ok = skipped = failed = 0

        # Pre-load recordings and ensure segments exist (DB work, main thread only).
        tasks: dict[int, dict] = {}
        segments_by_rec: dict[int, list] = {}
        for rec_id in recording_ids:
            rec = db.get(Recording, rec_id)
            if not _is_eligible(rec, rec_id):
                skipped += 1
                continue
            try:
                segs = ensure_segments(db, rec)
                db.commit()
            except Exception as exc:
                log.error("FAIL ensure_segments recording %d — %s", rec_id, exc)
                failed += 1
                continue
            tasks[rec_id] = _build_task(rec, segs)
            segments_by_rec[rec_id] = segs

        if not tasks:
            log.info("No eligible recordings after filtering.")
            log.info("Done. ok=%d skipped=%d failed=%d", ok, skipped, failed)
            return

        # Submit extraction tasks to thread pool; write results as they complete.
        future_to_rec_id: dict = {}
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for rec_id, task in tasks.items():
                future = pool.submit(
                    compute_embeddings,
                    task,
                    model,
                    model_lock,
                    args.batch_size,
                )
                future_to_rec_id[future] = rec_id

            for future in as_completed(future_to_rec_id):
                rec_id = future_to_rec_id[future]
                rec = db.get(Recording, rec_id)
                segs = segments_by_rec[rec_id]

                try:
                    result = future.result()
                    write_embeddings(result, segs, db)
                    db.commit()
                    mark_processed(db, rec_id, "clap", CLAP_VERSION)
                    n_embedded = len(result["embeddings"])
                    log.info("DONE recording %d — %d segments embedded", rec_id, n_embedded)
                    ok += 1
                except Exception as exc:
                    db.rollback()
                    mark_processed(
                        db, rec_id, "clap", CLAP_VERSION,
                        status="failed", error_message=str(exc),
                    )
                    log.error("FAIL recording %d — %s", rec_id, exc)
                    failed += 1

    finally:
        db.close()

    log.info("Done. ok=%d skipped=%d failed=%d", ok, skipped, failed)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
