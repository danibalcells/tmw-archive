"""Utilities for tracking pipeline processing state.

The processing_log table is an append-only log. Each call to mark_processed
adds a new row. needs_processing returns the IDs of recordings that have no
success entry for a given (step, version) pair, making it safe to call
repeatedly without double-processing.
"""

from datetime import datetime, timezone

from sqlalchemy import exists, select
from sqlalchemy.orm import Session as DBSession

from pipeline.db.models import ProcessingLog, Recording


def mark_processed(
    db: DBSession,
    recording_id: int,
    step: str,
    version: str,
    status: str = "success",
    error_message: str | None = None,
) -> ProcessingLog:
    entry = ProcessingLog(
        recording_id=recording_id,
        step=step,
        version=version,
        completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        status=status,
        error_message=error_message,
    )
    db.add(entry)
    db.commit()
    return entry


def needs_processing(db: DBSession, step: str, version: str) -> list[int]:
    """Return IDs of recordings with no success log entry for (step, version)."""
    success_subq = (
        select(ProcessingLog.recording_id)
        .where(
            ProcessingLog.step == step,
            ProcessingLog.version == version,
            ProcessingLog.status == "success",
        )
        .scalar_subquery()
    )
    rows = (
        db.execute(
            select(Recording.id).where(Recording.id.not_in(success_subq))
        )
        .scalars()
        .all()
    )
    return list(rows)
