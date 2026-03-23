"""One-off backfill: populate duration_seconds for recordings that lack it.

Pretrimmed recordings ingested before this change don't have duration_seconds
set. This script uses ffprobe on each recording's processed MP3 to fill it in.
Safe to run repeatedly — recordings that already have duration_seconds are
skipped.

Usage:
  python -m pipeline.scripts.backfill_durations [--dry-run]
"""

import argparse
import logging

from dotenv import load_dotenv

load_dotenv()

from pipeline.config import PROCESSED_ROOT
from pipeline.db.models import Recording
from pipeline.db.session import SessionLocal
from pipeline.ingest.transcode import get_audio_duration

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill duration_seconds for all recordings.")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    db = SessionLocal()

    try:
        missing = (
            db.query(Recording)
            .filter(Recording.duration_seconds.is_(None))
            .all()
        )
        log.info("Recordings without duration: %d", len(missing))

        ok = skipped = failed = 0

        for rec in missing:
            if rec.audio_path is None:
                log.warning("Recording %d has no audio_path — skipping", rec.id)
                skipped += 1
                continue

            audio_abs = PROCESSED_ROOT / rec.audio_path
            if not audio_abs.exists():
                log.warning("Audio file not found for recording %d: %s", rec.id, audio_abs)
                skipped += 1
                continue

            try:
                duration = round(get_audio_duration(audio_abs), 3)
            except Exception as exc:
                log.error("FAIL recording %d — %s", rec.id, exc)
                failed += 1
                continue

            if args.dry_run:
                log.info("DRY  recording %d → %.1fs (%s)", rec.id, duration, rec.audio_path)
                ok += 1
                continue

            rec.duration_seconds = duration
            db.commit()
            log.info("DONE recording %d → %.1fs", rec.id, duration)
            ok += 1

    finally:
        db.close()

    log.info("Done. ok=%d skipped=%d failed=%d", ok, skipped, failed)


if __name__ == "__main__":
    main()
