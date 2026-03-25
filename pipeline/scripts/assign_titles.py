from dotenv import load_dotenv

load_dotenv()

import argparse
import logging

from sqlalchemy.orm import joinedload

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
        description="Assign titles to recordings that have a linked song but no title."
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be updated without writing to the DB",
    )
    return p.parse_args()


def _build_title(song_title: str, session_date: str | None) -> str:
    if session_date:
        return f"{song_title} ({session_date})"
    return song_title


def main() -> None:
    args = _parse_args()
    db = SessionLocal()

    try:
        candidates = (
            db.query(Recording)
            .options(joinedload(Recording.song), joinedload(Recording.session))
            .filter(Recording.title.is_(None), Recording.song_id.isnot(None))
            .all()
        )

        updated = 0
        skipped = 0

        for rec in candidates:
            if rec.song is None:
                log.warning("Recording %d: song_id set but song missing — skipping", rec.id)
                skipped += 1
                continue

            session_date = rec.session.date if rec.session else None
            title = _build_title(rec.song.title, session_date)

            if args.dry_run:
                log.info(
                    "DRY  recording %d → title=%r (song=%r, date=%s)",
                    rec.id,
                    title,
                    rec.song.title,
                    session_date or "unknown",
                )
            else:
                rec.title = title
                log.info(
                    "SET  recording %d → title=%r",
                    rec.id,
                    title,
                )

            updated += 1

        if not args.dry_run:
            db.commit()

        log.info(
            "Done. updated=%d skipped=%d%s",
            updated,
            skipped,
            " (dry run)" if args.dry_run else "",
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()
