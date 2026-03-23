"""Segment creation and retrieval utilities.

Segments are fixed-length, overlapping windows over a recording used by all
downstream ML steps (librosa features, CLAP embeddings). They are geometry
only — no ML data lives here.

Window parameters:
  duration: 20 seconds
  step:     10 seconds (50% overlap)
"""

from sqlalchemy.orm import Session as DBSession

from pipeline.db.models import Recording, Segment

SEGMENT_DURATION = 20.0
SEGMENT_STEP = 10.0


def ensure_segments(db: DBSession, recording: Recording) -> list[Segment]:
    """Return segments for a recording, creating them if they don't exist yet.

    Idempotent: if segments already exist they are returned as-is. Raises
    ValueError if the recording has no duration_seconds set.
    """
    existing = (
        db.query(Segment)
        .filter(Segment.recording_id == recording.id)
        .order_by(Segment.start_seconds)
        .all()
    )
    if existing:
        return existing

    if recording.duration_seconds is None:
        raise ValueError(
            f"Recording {recording.id} has no duration_seconds — "
            "run backfill_durations.py first"
        )

    duration = recording.duration_seconds
    segments: list[Segment] = []
    start = 0.0
    while start < duration:
        end = min(start + SEGMENT_DURATION, duration)
        seg = Segment(
            recording_id=recording.id,
            start_seconds=round(start, 3),
            end_seconds=round(end, 3),
        )
        db.add(seg)
        segments.append(seg)
        start += SEGMENT_STEP

    db.flush()
    return segments
