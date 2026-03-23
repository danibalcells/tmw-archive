"""Runner: librosa feature extraction for all unprocessed recordings.

Queries the processing_log for recordings that don't yet have a successful
'librosa' step at the current version, runs feature extraction in parallel
using a process pool, and writes results back to the DB from the main process
(single SQLite writer). Safe to run repeatedly — already-processed recordings
are skipped automatically.

Usage:
  python -m pipeline.scripts.extract_librosa_features [options]

Options:
  --workers N       Worker processes (default: cpu_count - 1)
  --recording-id N  Process a single recording by ID
  --dry-run         Print what would be processed without writing to the DB
"""

import argparse
import logging
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

from dotenv import load_dotenv

load_dotenv()

from pipeline.config import PROCESSED_ROOT
from pipeline.db.models import Recording
from pipeline.db.processing import mark_processed, needs_processing
from pipeline.db.segments import ensure_segments
from pipeline.db.session import SessionLocal
from pipeline.features.librosa_features import compute_features, write_features

LIBROSA_VERSION = "1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract librosa features for recordings.")
    p.add_argument(
        "--workers",
        type=int,
        default=max(1, (os.cpu_count() or 2) - 1),
        help="Number of worker processes (default: cpu_count - 1)",
    )
    p.add_argument(
        "--recording-id",
        type=int,
        default=None,
        help="Process a single recording by ID (default: all unprocessed)",
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


def _is_eligible(recording: Recording, rec_id: int) -> bool:
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
            recording_ids = needs_processing(db, "librosa", LIBROSA_VERSION)

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

        ok = skipped = failed = 0
        workers = max(1, args.workers)
        log.info("Using %d worker process(es)", workers)

        # Pre-load recordings and ensure segments exist (DB work, main process only).
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

        # Submit all extraction tasks to the pool; write results as they complete.
        future_to_rec_id: dict = {}
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for rec_id, task in tasks.items():
                future = pool.submit(compute_features, task)
                future_to_rec_id[future] = rec_id

            for future in as_completed(future_to_rec_id):
                rec_id = future_to_rec_id[future]
                rec = db.get(Recording, rec_id)
                segs = segments_by_rec[rec_id]

                try:
                    result = future.result()
                    write_features(result, rec, segs, db)
                    db.commit()
                    mark_processed(db, rec_id, "librosa", LIBROSA_VERSION)
                    log.info("DONE recording %d — %d segments", rec_id, len(segs))
                    ok += 1
                except Exception as exc:
                    db.rollback()
                    mark_processed(
                        db, rec_id, "librosa", LIBROSA_VERSION,
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
