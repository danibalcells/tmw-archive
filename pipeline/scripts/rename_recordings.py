"""One-off migration: rename bare-ID MP3s to descriptive filenames.

Reads every Recording row whose audio_path points to a bare {id}.mp3 file,
builds the new label from the recording's metadata, renames the file on disk,
and updates audio_path in the DB.

Usage:
    python -m pipeline.scripts.rename_recordings           # dry run
    python -m pipeline.scripts.rename_recordings --apply   # rename for real
"""

import argparse
import logging
from pathlib import Path

from pipeline.config import PROCESSED_ROOT
from pipeline.db.models import Recording, Session, Song
from pipeline.db.session import SessionLocal
from pipeline.ingest.core import _pretrimmed_label, _vad_label
from pipeline.ingest.scanner import IngestItem
from pipeline.ingest.transcode import build_label

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_BARE_ID_SUFFIX = ".mp3"


def _is_bare_id(audio_path: str | None, recording_id: int) -> bool:
    if not audio_path:
        return False
    return Path(audio_path).name == f"{recording_id}.mp3"


def _build_label_for_recording(rec: Recording, db) -> str:
    if rec.origin == "vad_segment" and rec.source_path:
        start = rec.start_offset_seconds or 0.0
        end = rec.end_offset_seconds or 0.0
        return _vad_label(rec.source_path[0], start, end)

    song_slug: str | None = None
    if rec.song_id:
        song = db.get(Song, rec.song_id)
        if song:
            song_slug = song.slug

    date: str | None = None
    if rec.session_id:
        session = db.get(Session, rec.session_id)
        if session:
            date = session.date

    return build_label(song_slug or rec.title, date)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Actually rename files and update DB (default: dry run)")
    args = parser.parse_args()

    dry_run = not args.apply
    if dry_run:
        log.info("DRY RUN — pass --apply to rename for real")

    db = SessionLocal()
    try:
        recordings = db.query(Recording).filter(Recording.audio_path.isnot(None)).all()
        log.info("Found %d Recording(s) with audio_path set", len(recordings))

        renamed = 0
        skipped = 0
        errors = 0

        for rec in recordings:
            if not _is_bare_id(rec.audio_path, rec.id):
                log.debug("SKIP  id=%d  already has descriptive name: %s", rec.id, rec.audio_path)
                skipped += 1
                continue

            old_rel = rec.audio_path
            old_abs = PROCESSED_ROOT / old_rel

            if not old_abs.exists():
                log.warning("MISS  id=%d  file not found: %s", rec.id, old_abs)
                errors += 1
                continue

            label = _build_label_for_recording(rec, db)
            new_name = f"{rec.id}_{label}.mp3" if label else f"{rec.id}.mp3"
            new_abs = old_abs.parent / new_name
            new_rel = str(new_abs.relative_to(PROCESSED_ROOT))

            log.info(
                "%s  id=%d  %s  →  %s",
                "DRY" if dry_run else "REN",
                rec.id,
                old_abs.name,
                new_name,
            )

            if not dry_run:
                try:
                    old_abs.rename(new_abs)
                    rec.audio_path = new_rel
                    renamed += 1
                except OSError as exc:
                    log.error("FAIL  id=%d  %s", rec.id, exc)
                    errors += 1
            else:
                renamed += 1

        if not dry_run:
            db.commit()
            log.info("Committed DB updates.")

        log.info(
            "Done — %s: %d  skipped: %d  errors: %d",
            "would rename" if dry_run else "renamed",
            renamed,
            skipped,
            errors,
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()
