"""Ingest orchestrator: maps IngestItems to Session/Song/Recording rows.

For each IngestItem:
  1. Check for existing Recording rows (by source_path match).
  2. If found and not overwrite → skip.
  3. If found and overwrite → delete existing rows (cascades to Segments).
  4. Get-or-create Session by date.
  5. Get-or-create Song by slug+type (for sections that carry song identity).
  6. Pretrimmed: create one Recording, transcode, set audio_path.
  7. Raw: run VAD, create one Recording per segment, extract+transcode each.

All DB operations for a single IngestItem are committed atomically. A failure
in one item does not roll back previously committed items.
"""

import logging
from pathlib import Path

from sqlalchemy.orm import Session as DBSession

from pipeline.config import ARCHIVE_ROOT, PROCESSED_ROOT
from pipeline.db.models import Recording, Session, Song
from pipeline.db.processing import mark_processed
from pipeline.db.session import SessionLocal
from pipeline.ingest.scanner import IngestItem
from pipeline.ingest.transcode import build_label, transcode_full, transcode_segment
from pipeline.ingest.vad import detect_segments

INGEST_VERSION = "1"

log = logging.getLogger(__name__)


def _get_or_create_session(db: DBSession, date: str, date_uncertain: bool, notes: str | None) -> Session:
    obj = db.query(Session).filter(Session.date == date).first()
    if obj:
        return obj
    obj = Session(date=date, date_uncertain=date_uncertain, notes=notes)
    db.add(obj)
    db.flush()
    log.info("Created Session date=%s", date)
    return obj


def _get_or_create_song(db: DBSession, slug: str, song_type: str, title: str) -> Song:
    obj = db.query(Song).filter(Song.slug == slug).first()
    if obj:
        return obj
    obj = Song(slug=slug, song_type=song_type, title=title)
    db.add(obj)
    db.flush()
    log.info("Created Song slug=%s type=%s", slug, song_type)
    return obj


def _existing_recordings(db: DBSession, source_paths: list[str]) -> list[Recording]:
    return (
        db.query(Recording)
        .filter(Recording.source_path == source_paths)
        .all()
    )


def _delete_recordings(db: DBSession, recordings: list[Recording]) -> None:
    for rec in recordings:
        db.delete(rec)
    db.flush()
    log.info("Deleted %d existing Recording(s)", len(recordings))


def _rel_audio_path(abs_path: Path) -> str:
    return str(abs_path.relative_to(PROCESSED_ROOT))


def _seconds_to_timecode(seconds: float) -> str:
    """Convert seconds to a compact timecode string: 0h02m15s."""
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h}h{m:02d}m{s:02d}s"


def _pretrimmed_label(item: IngestItem) -> str:
    """Build a human-readable filename label for a pretrimmed recording."""
    return build_label(item.song_slug or item.title, item.date)


def _vad_label(source_path: str, start_sec: float, end_sec: float) -> str:
    """Build a filename label for a VAD segment."""
    source_stem = Path(source_path).stem
    start_tc = _seconds_to_timecode(start_sec)
    end_tc = _seconds_to_timecode(end_sec)
    return build_label(source_stem, f"{start_tc}-{end_tc}")


def ingest_item(
    item: IngestItem,
    db: DBSession,
    overwrite: bool = False,
    dry_run: bool = False,
) -> int:
    """Process a single IngestItem. Returns number of Recording rows created."""
    existing = _existing_recordings(db, item.source_paths)

    if existing:
        if not overwrite:
            log.info("SKIP  %s (already ingested, %d recording(s))", item.source_paths, len(existing))
            return 0
        if not dry_run:
            _delete_recordings(db, existing)
        else:
            log.info("DRY   would delete %d existing recording(s) for %s", len(existing), item.source_paths)

    session_obj: Session | None = None
    if item.date and not dry_run:
        session_obj = _get_or_create_session(
            db, item.date, item.date_uncertain, item.notes
        )
    elif item.date and dry_run:
        log.info("DRY   would get-or-create Session date=%s", item.date)

    song_obj: Song | None = None
    if item.song_slug and item.song_type and not dry_run:
        song_obj = _get_or_create_song(
            db,
            item.song_slug,
            item.song_type,
            item.title or item.song_slug,
        )
    elif item.song_slug and dry_run:
        log.info("DRY   would get-or-create Song slug=%s", item.song_slug)

    if item.origin == "pretrimmed":
        return _ingest_pretrimmed(item, db, session_obj, song_obj, dry_run)
    else:
        return _ingest_raw(item, db, session_obj, song_obj, dry_run)


def _ingest_pretrimmed(
    item: IngestItem,
    db: DBSession,
    session_obj: Session | None,
    song_obj: Song | None,
    dry_run: bool,
) -> int:
    source_abs = ARCHIVE_ROOT / item.source_paths[0]

    if dry_run:
        log.info(
            "DRY   pretrimmed %s → 1 Recording (session=%s, song=%s)",
            item.source_paths[0],
            item.date,
            item.song_slug,
        )
        return 1

    rec = Recording(
        session_id=session_obj.id if session_obj else None,
        song_id=song_obj.id if song_obj else None,
        title=item.title,
        source_path=item.source_paths,
        origin="pretrimmed",
    )
    db.add(rec)
    db.flush()

    audio_abs = transcode_full(source_abs, rec.id, label=_pretrimmed_label(item))
    rec.audio_path = _rel_audio_path(audio_abs)
    db.commit()

    mark_processed(db, rec.id, "ingest", INGEST_VERSION)
    log.info("DONE  pretrimmed %s → Recording id=%d", item.source_paths[0], rec.id)
    return 1


def _ingest_raw(
    item: IngestItem,
    db: DBSession,
    session_obj: Session | None,
    song_obj: Song | None,
    dry_run: bool,
) -> int:
    source_abs = ARCHIVE_ROOT / item.source_paths[0]

    if dry_run:
        log.info(
            "DRY   raw %s → VAD segmentation (session=%s)",
            item.source_paths[0],
            item.date,
        )
        return 0

    segments = detect_segments(source_abs)

    if not segments:
        log.warning("VAD returned no segments for %s — skipping", item.source_paths[0])
        return 0

    created = 0
    for start_sec, end_sec in segments:
        rec = Recording(
            session_id=session_obj.id if session_obj else None,
            song_id=None,
            title=None,
            source_path=item.source_paths,
            origin="vad_segment",
            start_offset_seconds=start_sec,
            end_offset_seconds=end_sec,
            duration_seconds=round(end_sec - start_sec, 3),
        )
        db.add(rec)
        db.flush()

        audio_abs = transcode_segment(
            source_abs, rec.id, start_sec, end_sec,
            label=_vad_label(item.source_paths[0], start_sec, end_sec),
        )
        rec.audio_path = _rel_audio_path(audio_abs)
        db.commit()

        mark_processed(db, rec.id, "ingest", INGEST_VERSION)
        created += 1

    log.info(
        "DONE  raw %s → %d vad_segment Recording(s)",
        item.source_paths[0],
        created,
    )
    return created


def run_ingest(
    items: list[IngestItem],
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict[str, int]:
    """Ingest all items. Returns a summary dict."""
    db = SessionLocal()
    total_created = 0
    total_skipped = 0
    total_failed = 0

    try:
        for item in items:
            try:
                created = ingest_item(item, db, overwrite=overwrite, dry_run=dry_run)
                if created == 0 and not dry_run:
                    total_skipped += 1
                else:
                    total_created += created
            except Exception as exc:
                log.error("FAIL  %s — %s", item.source_paths, exc)
                db.rollback()
                total_failed += 1
    finally:
        db.close()

    return {"created": total_created, "skipped": total_skipped, "failed": total_failed}
