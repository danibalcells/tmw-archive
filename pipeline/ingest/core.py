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
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session as DBSession

from pipeline.config import ARCHIVE_ROOT, PROCESSED_ROOT
from pipeline.db.models import Recording, Session, Song
from pipeline.db.processing import mark_processed
from pipeline.db.session import SessionLocal
from pipeline.ingest.scanner import IngestItem
from pipeline.ingest.transcode import build_label, get_audio_duration, transcode_full, transcode_segment
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
        if rec.audio_path:
            audio_abs = PROCESSED_ROOT / rec.audio_path
            if audio_abs.exists():
                audio_abs.unlink()
                log.debug("Deleted audio file %s", audio_abs)
            else:
                log.debug("Audio file not found on disk (already gone?): %s", audio_abs)
        db.delete(rec)
    db.flush()
    log.info("Deleted %d existing Recording(s) (and their audio files)", len(recordings))


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
    ingest_config: dict[str, Any] | None = None,
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
            log.info("DRY   would delete %d existing recording(s) and audio files for %s", len(existing), item.source_paths)

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
        return _ingest_raw(item, db, session_obj, song_obj, dry_run, ingest_config)


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
        duration_seconds=round(get_audio_duration(source_abs), 3),
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
    ingest_config: dict[str, Any] | None = None,
) -> int:
    source_abs = ARCHIVE_ROOT / item.source_paths[0]

    if dry_run:
        log.info(
            "DRY   raw %s → VAD segmentation (session=%s)",
            item.source_paths[0],
            item.date,
        )
        return 0

    segments = detect_segments(source_abs, config=ingest_config)

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


def _vad_task(task: dict) -> dict:
    """Worker: run VAD on a raw source file. Returns detected segments."""
    segments = detect_segments(Path(task["source_abs"]), config=task["config"])
    return {"segments": segments}


def _duration_task(task: dict) -> dict:
    """Worker: probe duration of a pretrimmed source file."""
    duration = get_audio_duration(Path(task["source_abs"]))
    return {"duration": duration}


def _transcode_task(task: dict) -> dict:
    """Worker: transcode a recording (full file or segment). Returns relative audio_path."""
    source = Path(task["source_abs"])
    rec_id: int = task["recording_id"]
    label: str = task["label"]
    if task["type"] == "full":
        audio_abs = transcode_full(source, rec_id, label=label)
    else:
        audio_abs = transcode_segment(source, rec_id, task["start_sec"], task["end_sec"], label=label)
    return {"recording_id": rec_id, "audio_path": str(audio_abs.relative_to(PROCESSED_ROOT))}


def _run_ingest_parallel(
    items: list[IngestItem],
    overwrite: bool,
    ingest_config: dict[str, Any] | None,
    workers: int,
) -> dict[str, int]:
    """Parallel ingest: VAD/duration in pool → DB creates → transcode in pool → DB updates."""
    db = SessionLocal()
    total_created = 0
    total_skipped = 0
    total_failed = 0

    try:
        eligible: list[IngestItem] = []
        for item in items:
            existing = _existing_recordings(db, item.source_paths)
            if existing:
                if not overwrite:
                    log.info("SKIP  %s (already ingested, %d recording(s))", item.source_paths, len(existing))
                    total_skipped += 1
                    continue
                _delete_recordings(db, existing)
            eligible.append(item)

        if not eligible:
            return {"created": total_created, "skipped": total_skipped, "failed": total_failed}

        log.info("Phase 1: pre-compute (VAD / duration) for %d item(s) using %d worker(s)", len(eligible), workers)

        vad_results: dict[int, list[tuple[float, float]]] = {}
        duration_results: dict[int, float] = {}
        phase1_failed: set[int] = set()

        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_to_idx: dict = {}
            for idx, item in enumerate(eligible):
                source_abs = str(ARCHIVE_ROOT / item.source_paths[0])
                if item.origin == "raw":
                    future = pool.submit(_vad_task, {"source_abs": source_abs, "config": ingest_config})
                else:
                    future = pool.submit(_duration_task, {"source_abs": source_abs})
                future_to_idx[future] = idx

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                item = eligible[idx]
                try:
                    result = future.result()
                    if item.origin == "raw":
                        vad_results[idx] = result["segments"]
                    else:
                        duration_results[idx] = result["duration"]
                except Exception as exc:
                    log.error("FAIL  pre-compute %s — %s", item.source_paths, exc)
                    phase1_failed.add(idx)
                    total_failed += 1

        log.info("Phase 2: creating Recording rows")

        transcode_tasks: list[dict] = []
        for idx, item in enumerate(eligible):
            if idx in phase1_failed:
                continue

            source_abs = ARCHIVE_ROOT / item.source_paths[0]

            session_obj: Session | None = None
            if item.date:
                session_obj = _get_or_create_session(db, item.date, item.date_uncertain, item.notes)

            song_obj: Song | None = None
            if item.song_slug and item.song_type:
                song_obj = _get_or_create_song(db, item.song_slug, item.song_type, item.title or item.song_slug)

            if item.origin == "pretrimmed":
                duration = duration_results.get(idx, 0.0)
                rec = Recording(
                    session_id=session_obj.id if session_obj else None,
                    song_id=song_obj.id if song_obj else None,
                    title=item.title,
                    source_path=item.source_paths,
                    origin="pretrimmed",
                    duration_seconds=round(duration, 3),
                )
                db.add(rec)
                db.flush()
                transcode_tasks.append({
                    "type": "full",
                    "source_abs": str(source_abs),
                    "recording_id": rec.id,
                    "label": _pretrimmed_label(item),
                })
            else:
                segments = vad_results.get(idx, [])
                if not segments:
                    log.warning("VAD returned no segments for %s — skipping", item.source_paths[0])
                    continue
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
                    transcode_tasks.append({
                        "type": "segment",
                        "source_abs": str(source_abs),
                        "recording_id": rec.id,
                        "start_sec": start_sec,
                        "end_sec": end_sec,
                        "label": _vad_label(item.source_paths[0], start_sec, end_sec),
                    })

        db.commit()
        log.info("Phase 3: transcoding %d recording(s) using %d worker(s)", len(transcode_tasks), workers)

        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_to_task: dict = {}
            for task in transcode_tasks:
                future = pool.submit(_transcode_task, task)
                future_to_task[future] = task

            for future in as_completed(future_to_task):
                task = future_to_task[future]
                rec_id: int = task["recording_id"]
                try:
                    result = future.result()
                    rec = db.get(Recording, rec_id)
                    rec.audio_path = result["audio_path"]
                    db.commit()
                    mark_processed(db, rec_id, "ingest", INGEST_VERSION)
                    log.info("DONE  recording %d — %s", rec_id, result["audio_path"])
                    total_created += 1
                except Exception as exc:
                    log.error("FAIL  transcode recording %d — %s", rec_id, exc)
                    db.rollback()
                    total_failed += 1

    finally:
        db.close()

    return {"created": total_created, "skipped": total_skipped, "failed": total_failed}


def run_ingest(
    items: list[IngestItem],
    overwrite: bool = False,
    dry_run: bool = False,
    ingest_config: dict[str, Any] | None = None,
    workers: int = 1,
) -> dict[str, int]:
    """Ingest all items. Returns a summary dict."""
    if dry_run or workers <= 1:
        db = SessionLocal()
        total_created = 0
        total_skipped = 0
        total_failed = 0

        try:
            for item in items:
                try:
                    created = ingest_item(item, db, overwrite=overwrite, dry_run=dry_run, ingest_config=ingest_config)
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

    return _run_ingest_parallel(items, overwrite=overwrite, ingest_config=ingest_config, workers=workers)
