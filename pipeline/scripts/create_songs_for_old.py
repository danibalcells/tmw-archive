"""One-time script: create Song rows for pretrimmed Old/ recordings that have no song_id.

Each Old/ recording is a pretrimmed take of a specific composition. The archive
scanner intentionally left song_type=None for Old/ because the folder structure
doesn't distinguish between songs, covers, and jams. This script creates one Song
per recording and links them.

Usage:
    python -m pipeline.scripts.create_songs_for_old [--song-type original] [--dry-run]
"""

import argparse
import logging
import re

from dotenv import load_dotenv

load_dotenv()

from pipeline.db.models import Recording, Song
from pipeline.db.session import SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

VALID_SONG_TYPES = ("original", "cover", "jam")


def _slugify(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r"[àáâãäå]", "a", slug)
    slug = re.sub(r"[èéêë]", "e", slug)
    slug = re.sub(r"[ìíîï]", "i", slug)
    slug = re.sub(r"[òóôõö]", "o", slug)
    slug = re.sub(r"[ùúûü]", "u", slug)
    slug = re.sub(r"[ç]", "c", slug)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


def _unique_slug(db, base_slug: str) -> str:
    slug = base_slug
    n = 1
    while db.query(Song).filter(Song.slug == slug).first():
        slug = f"{base_slug}-{n}"
        n += 1
    return slug


def main() -> None:
    p = argparse.ArgumentParser(description="Create Songs for unlinked Old/ recordings.")
    p.add_argument(
        "--song-type",
        default="original",
        choices=VALID_SONG_TYPES,
        help="song_type to assign (default: original)",
    )
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    db = SessionLocal()
    try:
        recordings = (
            db.query(Recording)
            .filter(
                Recording.song_id.is_(None),
                Recording.origin == "pretrimmed",
                Recording.title.is_not(None),
            )
            .order_by(Recording.title)
            .all()
        )

        log.info("Found %d pretrimmed recordings with no song_id", len(recordings))

        created = skipped = 0
        for rec in recordings:
            title = rec.title
            base_slug = _slugify(title)

            existing = db.query(Song).filter(Song.slug == base_slug).first()
            if existing:
                log.info("SKIP  '%s' — song slug '%s' already exists (id=%d)", title, base_slug, existing.id)
                if not args.dry_run:
                    rec.song_id = existing.id
                    db.commit()
                skipped += 1
                continue

            slug = _unique_slug(db, base_slug)
            log.info(
                "%s  '%s' → slug='%s' type='%s'",
                "DRY " if args.dry_run else "CREATE",
                title,
                slug,
                args.song_type,
            )

            if not args.dry_run:
                song = Song(title=title, slug=slug, song_type=args.song_type)
                db.add(song)
                db.flush()
                rec.song_id = song.id
                db.commit()

            created += 1

        log.info("Done. created=%d linked_existing=%d", created, skipped)

    finally:
        db.close()


if __name__ == "__main__":
    main()
