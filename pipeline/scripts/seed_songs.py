"""Seed the songs table from Temas/ and Covers/ folder structure.

Temas/<FolderName>/  →  Song(song_type='original', slug=FolderName)
Covers/<FolderName>/ →  Song(song_type='cover',    slug=FolderName)

Idempotent: existing slugs are skipped. Safe to re-run.

Usage:
    python -m pipeline.scripts.seed_songs
"""

import re
from pathlib import Path

from pipeline.config import ARCHIVE_ROOT
from pipeline.db.models import Song
from pipeline.db.session import SessionLocal


def humanize(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).title()


def seed_section(archive_path: Path, song_type: str, db) -> int:
    count = 0
    for folder in sorted(archive_path.iterdir()):
        if not folder.is_dir():
            continue
        slug = folder.name
        if db.query(Song).filter(Song.slug == slug).first():
            continue
        db.add(Song(title=humanize(slug), slug=slug, song_type=song_type))
        count += 1
    db.commit()
    return count


def main() -> None:
    db = SessionLocal()
    try:
        originals = seed_section(ARCHIVE_ROOT / "Temas", "original", db)
        covers = seed_section(ARCHIVE_ROOT / "Covers", "cover", db)
        print(f"Seeded {originals} original(s), {covers} cover(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
