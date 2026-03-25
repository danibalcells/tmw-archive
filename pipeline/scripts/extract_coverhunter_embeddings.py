"""Runner: CoverHunter embedding extraction for all unprocessed recordings.

Loads one CoverHunterMPS model in the main process, then processes recordings
in parallel using a thread pool. Each thread:
  1. Loads and decodes its recording's audio + computes CQT (I/O + CPU, concurrent)
  2. Acquires a shared lock to run model inference (serialized per call)
  3. Returns the (128,) float32 embedding; main thread packs, writes, and commits

Parallelism benefit: while one thread runs inference, others decode audio —
the bottleneck alternates between I/O and compute, keeping CPU busy throughout.

Usage:
  python -m pipeline.scripts.extract_coverhunter_embeddings [options]

Options:
  --workers N          Thread count (default: 4)
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
from pipeline.db.session import SessionLocal
from pipeline.features.coverhunter import _load_feat, _run_inference, pack_embedding, load_model
from pipeline.ingest.tiers import filter_recording_ids_by_tier, tier_paths_for

COVERHUNTER_VERSION = "1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract CoverHunter embeddings for recordings.")
    p.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Thread count (default: 4)",
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


def _extract(audio_abs: str, model: dict, model_lock: threading.Lock) -> bytes:
    """Audio loading + CQT (concurrent) then inference (serialized) then pack."""
    feat = _load_feat(audio_abs, model)
    with model_lock:
        vec = _run_inference(feat, model)
    return pack_embedding(vec)


def main() -> None:
    args = _parse_args()
    db = SessionLocal()

    try:
        if args.recording_id is not None:
            recording_ids = [args.recording_id]
        else:
            recording_ids = needs_processing(db, "coverhunter", COVERHUNTER_VERSION)

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

        log.info("Loading CoverHunter model…")
        model = load_model()
        model_lock = threading.Lock()
        log.info("CoverHunter model loaded. Using %d worker thread(s).", args.workers)

        ok = skipped = failed = 0

        tasks: dict[int, str] = {}
        for rec_id in recording_ids:
            rec = db.get(Recording, rec_id)
            if not _is_eligible(rec, rec_id):
                skipped += 1
                continue
            tasks[rec_id] = str(PROCESSED_ROOT / rec.audio_path)

        if not tasks:
            log.info("No eligible recordings after filtering.")
            log.info("Done. ok=%d skipped=%d failed=%d", ok, skipped, failed)
            return

        future_to_rec_id: dict = {}
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for rec_id, audio_abs in tasks.items():
                future = pool.submit(_extract, audio_abs, model, model_lock)
                future_to_rec_id[future] = rec_id

            for future in as_completed(future_to_rec_id):
                rec_id = future_to_rec_id[future]
                rec = db.get(Recording, rec_id)

                try:
                    packed = future.result()
                    rec.coverhunter_embedding = packed
                    db.commit()
                    mark_processed(db, rec_id, "coverhunter", COVERHUNTER_VERSION)
                    log.info("DONE recording %d (%s)", rec_id, rec.audio_path)
                    ok += 1
                except Exception as exc:
                    db.rollback()
                    mark_processed(
                        db, rec_id, "coverhunter", COVERHUNTER_VERSION,
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
