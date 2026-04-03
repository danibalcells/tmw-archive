"""Runner: MERT embedding extraction for all unprocessed recordings.

Loads one MERT-v1-330M model, then processes recordings using a thread pool.
Each thread loads audio independently, acquires a lock for model inference.

Usage:
  python -m pipeline.scripts.extract_mert_embeddings [options]

Options:
  --workers N          Thread count (default: 2)
  --device DEVICE      cpu / mps / cuda (default: cpu)
  --recording-id N     Process a single recording by ID
  --tier N [N ...]     Restrict to dev subset(s)
  --dry-run            Print what would be processed without writing
"""

import argparse
import logging
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
from pipeline.features.mert_embeddings import compute_embeddings, load_model, write_embeddings
from pipeline.ingest.tiers import filter_recording_ids_by_tier, tier_paths_for

MERT_VERSION = "1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract MERT embeddings for recordings.")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--device", type=str, default="cpu", choices=["cpu", "mps", "cuda"])
    p.add_argument("--recording-id", type=int, default=None)
    p.add_argument("--tier", type=int, nargs="+", choices=[1, 2], metavar="N", default=None)
    p.add_argument("--dry-run", action="store_true")
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
        log.warning("Recording %d has no duration_seconds — skipping", rec_id)
        return False
    return True


def main() -> None:
    args = _parse_args()
    db = SessionLocal()

    try:
        if args.recording_id is not None:
            recording_ids = [args.recording_id]
        else:
            recording_ids = needs_processing(db, "mert", MERT_VERSION)

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

        log.info("Loading MERT model on %s…", args.device)
        model, processor = load_model(args.device)
        model_lock = threading.Lock()
        log.info("MERT model loaded. Using %d worker thread(s)", args.workers)

        ok = skipped = failed = 0

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

        future_to_rec_id: dict = {}
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for rec_id, task in tasks.items():
                future = pool.submit(
                    compute_embeddings,
                    task, model, processor, model_lock, args.device,
                )
                future_to_rec_id[future] = rec_id

            for future in as_completed(future_to_rec_id):
                rec_id = future_to_rec_id[future]
                segs = segments_by_rec[rec_id]

                try:
                    result = future.result()
                    write_embeddings(result, segs, db)
                    db.commit()
                    mark_processed(db, rec_id, "mert", MERT_VERSION)
                    n_embedded = len(result["embeddings"])
                    log.info("DONE recording %d — %d segments embedded", rec_id, n_embedded)
                    ok += 1
                except Exception as exc:
                    db.rollback()
                    mark_processed(
                        db, rec_id, "mert", MERT_VERSION,
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
