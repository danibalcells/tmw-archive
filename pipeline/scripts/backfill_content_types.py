from dotenv import load_dotenv

load_dotenv()

import argparse
import logging

from pipeline.db.models import Recording
from pipeline.db.session import SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Backfill content_type for existing pretrimmed recordings."
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be updated without writing to the DB",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    db = SessionLocal()

    try:
        all_pretrimmed = (
            db.query(Recording).filter(Recording.origin == "pretrimmed").all()
        )
        already_set = sum(1 for r in all_pretrimmed if r.content_type is not None)
        candidates = [r for r in all_pretrimmed if r.content_type is None]

        updated = 0
        no_song = 0

        for rec in candidates:
            song = rec.song
            if song is None:
                log.warning("Recording %d: no associated song — skipping", rec.id)
                no_song += 1
                continue

            if song.song_type in ("original", "cover"):
                content_type = "song_take"
            elif song.song_type == "jam":
                content_type = "jam"
            else:
                log.warning(
                    "Recording %d: unhandled song_type %r — skipping",
                    rec.id,
                    song.song_type,
                )
                no_song += 1
                continue

            if args.dry_run:
                log.info(
                    "DRY  recording %d → content_type=%s (song=%s, song_type=%s)",
                    rec.id,
                    content_type,
                    song.slug,
                    song.song_type,
                )
            else:
                rec.content_type = content_type
                rec.content_type_source = "ingest"

            updated += 1

        if not args.dry_run:
            db.commit()

        log.info(
            "Done. total_pretrimmed=%d already_set=%d updated=%d no_song=%d%s",
            len(all_pretrimmed),
            already_set,
            updated,
            no_song,
            " (dry run)" if args.dry_run else "",
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()
