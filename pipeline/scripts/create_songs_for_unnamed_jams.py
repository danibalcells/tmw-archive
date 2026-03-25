"""Backfill: create Song rows for recordings with content_type='jam' and no song_id.

For each such recording, creates a Song with song_type='jam', auto-titled from the
session date (e.g. "Jam — 2016-01-17"), and links the recording via song_id.

Usage:
    python -m pipeline.scripts.create_songs_for_unnamed_jams [--dry-run]
"""

import argparse
import logging
import re

from dotenv import load_dotenv

load_dotenv()

from pipeline.config import PROCESSED_ROOT
from pipeline.db.models import Recording, Song
from pipeline.db.session import SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _generate_slug(title: str, db) -> str:
    base_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = base_slug
    counter = 1
    while db.query(Song).filter(Song.slug == slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def _jam_title(session_date: str | None, db) -> str:
    base = f"Jam \u2014 {session_date}" if session_date else "Jam \u2014 unknown"
    existing_count = (
        db.query(Song)
        .filter(Song.song_type == "jam", Song.title.like(f"{base}%"))
        .count()
    )
    return base if existing_count == 0 else f"{base} #{existing_count + 1}"


def _rename_audio(rec: Recording, song: Song) -> None:
    if not rec.audio_path:
        return
    new_filename = f"{rec.id}_{song.slug}.mp3"
    old_path = PROCESSED_ROOT / rec.audio_path
    new_path = old_path.parent / new_filename
    if not old_path.exists():
        log.warning("Audio file not found for rename: %s", old_path)
        return
    if old_path == new_path:
        return
    log.info("Renaming %s → %s", old_path.name, new_path.name)
    old_path.rename(new_path)
    rec.audio_path = str(new_path.relative_to(PROCESSED_ROOT))


def main() -> None:
    p = argparse.ArgumentParser(
        description="Create Song rows for unnamed jam recordings."
    )
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    db = SessionLocal()
    try:
        recordings = (
            db.query(Recording)
            .filter(
                Recording.content_type == "jam",
                Recording.song_id.is_(None),
            )
            .all()
        )

        log.info("Found %d jam recordings with no song_id", len(recordings))

        created = 0
        for rec in recordings:
            session_date = rec.session.date if rec.session else None
            title = _jam_title(session_date, db)
            slug = _generate_slug(title, db)

            log.info(
                "%s  recording %d \u2192 '%s' (slug=%s, session=%s)",
                "DRY " if args.dry_run else "CREATE",
                rec.id,
                title,
                slug,
                session_date,
            )

            if not args.dry_run:
                song = Song(title=title, slug=slug, song_type="jam")
                db.add(song)
                db.flush()
                rec.song_id = song.id
                _rename_audio(rec, song)
                db.commit()

            created += 1

        log.info("Done. created=%d%s", created, " (dry run)" if args.dry_run else "")

    finally:
        db.close()


if __name__ == "__main__":
    main()
