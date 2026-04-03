import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, update
from sqlalchemy.orm import Session, aliased

from pipeline.config import PROCESSED_ROOT

MOOD_MAP_BASE_DIR = Path("data/umaps")
MOOD_MAP_KINDS = {"segments", "recording-passage"}
PASSAGES_BASE_DIR = Path("data/eternal-rehearsal")
from pipeline.db.models import FeatureTimeseries, ProcessingLog, Recording, Segment, Session as DbSession, Song, SongMatchCandidate
from pipeline.db.session import get_session
from pipeline.features.clap_embeddings import unpack_embedding
from pipeline.features.faiss_index import DEFAULT_INDEX_PATH, load_index, search
from pipeline.scripts.match_songs import run_matching

log = logging.getLogger(__name__)

app = FastAPI(title="TMW Archive API")

_faiss_index = None


def _get_faiss_index():
    global _faiss_index
    if _faiss_index is None:
        _faiss_index = load_index(DEFAULT_INDEX_PATH)
    return _faiss_index


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:4173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

VALID_CONTENT_TYPES = {
    "song_take", "jam", "banter", "tuning", "noodling", "silence", "count_in", "other"
}
VALID_SONG_TYPES = {"original", "cover", "jam"}


class RecordingOut(BaseModel):
    id: int
    title: Optional[str]
    origin: str
    duration_seconds: Optional[float]
    session_date: Optional[str]
    song_id: Optional[int]
    song_title: Optional[str]
    audio_path: Optional[str]

    model_config = {"from_attributes": True}


class CandidateOut(BaseModel):
    id: int
    song_id: int
    song_title: str
    confidence: float
    rank: int
    status: str
    nearest_recording_id: int
    nearest_recording_audio_path: Optional[str]
    nearest_recording_session_date: Optional[str]
    model_config = {"from_attributes": True}


class ReviewRecordingOut(BaseModel):
    id: int
    title: Optional[str]
    origin: str
    duration_seconds: Optional[float]
    session_date: Optional[str]
    audio_path: Optional[str]
    content_type: Optional[str]
    content_type_source: Optional[str]
    song_id: Optional[int]
    song_title: Optional[str]
    candidates: list[CandidateOut]
    mean_rms: Optional[float]
    mean_spectral_centroid: Optional[float]
    model_config = {"from_attributes": True}


class ClassifyBody(BaseModel):
    content_type: str
    song_id: Optional[int] = None


class AssignSongBody(BaseModel):
    song_id: int


class BatchClassifyBody(BaseModel):
    recording_ids: list[int]
    content_type: str


class CreateSongBody(BaseModel):
    title: str
    song_type: str = "original"


class SplitBody(BaseModel):
    split_at: float


class SplitResult(BaseModel):
    actual_split_at: float
    recordings: list[ReviewRecordingOut]


class SongOut(BaseModel):
    id: int
    title: str
    slug: str
    song_type: str
    cover_of: Optional[str]
    recording_count: int

    model_config = {"from_attributes": True}


class SongDetailOut(BaseModel):
    id: int
    title: str
    slug: str
    song_type: str
    cover_of: Optional[str]
    recordings: list[RecordingOut]

    model_config = {"from_attributes": True}


class SessionOut(BaseModel):
    id: int
    date: str
    date_uncertain: bool
    notes: Optional[str]
    recording_count: int

    model_config = {"from_attributes": True}


class SessionDetailOut(BaseModel):
    id: int
    date: str
    date_uncertain: bool
    notes: Optional[str]
    recordings: list[RecordingOut]

    model_config = {"from_attributes": True}


class SimilarSegmentOut(BaseModel):
    segment_id: int
    recording_id: int
    start_seconds: float
    end_seconds: float
    score: float
    recording_title: Optional[str]
    session_date: Optional[str]
    audio_path: Optional[str]


def _recording_out(rec: Recording) -> RecordingOut:
    return RecordingOut(
        id=rec.id,
        title=rec.title,
        origin=rec.origin,
        duration_seconds=rec.duration_seconds,
        session_date=rec.session.date if rec.session else None,
        song_id=rec.song_id,
        song_title=rec.song.title if rec.song else None,
        audio_path=rec.audio_path,
    )


def _candidate_out(c: SongMatchCandidate) -> CandidateOut:
    nr = c.nearest_recording
    return CandidateOut(
        id=c.id,
        song_id=c.song_id,
        song_title=c.song.title,
        confidence=c.confidence,
        rank=c.rank,
        status=c.status,
        nearest_recording_id=c.nearest_recording_id,
        nearest_recording_audio_path=nr.audio_path if nr else None,
        nearest_recording_session_date=nr.session.date if nr and nr.session else None,
    )


def _review_recording_out(rec: Recording, db: Session) -> ReviewRecordingOut:
    candidates = sorted(
        [c for c in rec.song_match_candidates if c.status != "rejected"],
        key=lambda c: c.rank,
    )
    mean_rms = (
        db.query(func.avg(Segment.mean_rms))
        .filter(Segment.recording_id == rec.id)
        .scalar()
    )
    mean_sc = (
        db.query(func.avg(Segment.mean_spectral_centroid))
        .filter(Segment.recording_id == rec.id)
        .scalar()
    )
    return ReviewRecordingOut(
        id=rec.id,
        title=rec.title,
        origin=rec.origin,
        duration_seconds=rec.duration_seconds,
        session_date=rec.session.date if rec.session else None,
        audio_path=rec.audio_path,
        content_type=rec.content_type,
        content_type_source=rec.content_type_source,
        song_id=rec.song_id,
        song_title=rec.song.title if rec.song else None,
        candidates=[_candidate_out(c) for c in candidates],
        mean_rms=mean_rms,
        mean_spectral_centroid=mean_sc,
    )


def _rename_recording_file(rec: Recording, db: Session) -> None:
    if not rec.audio_path or not rec.song_id:
        return
    song = db.query(Song).filter(Song.id == rec.song_id).first()
    if not song:
        return
    new_filename = f"{rec.id}_{song.slug}.mp3"
    old_path = PROCESSED_ROOT / rec.audio_path
    new_path = old_path.parent / new_filename
    if not old_path.exists():
        log.warning("Audio file not found for rename: %s", old_path)
        return
    log.info("Renaming %s → %s", old_path, new_path)
    old_path.rename(new_path)
    rec.audio_path = str(new_path.relative_to(PROCESSED_ROOT))


def _generate_slug(title: str, db: Session) -> str:
    base_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = base_slug
    counter = 1
    while db.query(Song).filter(Song.slug == slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def _jam_auto_title(session_date: str | None, db: Session) -> str:
    base = f"Jam \u2014 {session_date}" if session_date else "Jam \u2014 unknown"
    existing_count = (
        db.query(Song)
        .filter(Song.song_type == "jam", Song.title.like(f"{base}%"))
        .count()
    )
    return base if existing_count == 0 else f"{base} #{existing_count + 1}"


@app.get("/api/songs", response_model=list[SongDetailOut])
def list_songs(db: Session = Depends(get_session)) -> list[SongDetailOut]:
    songs = (
        db.query(Song)
        .filter(Song.song_type.in_(["original", "cover"]))
        .order_by(Song.title)
        .all()
    )
    return [
        SongDetailOut(
            id=s.id,
            title=s.title,
            slug=s.slug,
            song_type=s.song_type,
            cover_of=s.cover_of,
            recordings=[_recording_out(r) for r in s.recordings],
        )
        for s in songs
    ]


@app.post("/api/songs", response_model=SongDetailOut)
def create_song(
    body: CreateSongBody,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
) -> SongDetailOut:
    if body.song_type not in VALID_SONG_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid song_type '{body.song_type}'")
    slug = _generate_slug(body.title, db)
    song = Song(title=body.title, slug=slug, song_type=body.song_type)
    db.add(song)
    db.commit()
    db.refresh(song)
    background_tasks.add_task(run_matching)
    return SongDetailOut(
        id=song.id,
        title=song.title,
        slug=song.slug,
        song_type=song.song_type,
        cover_of=song.cover_of,
        recordings=[],
    )


@app.get("/api/songs/{song_id}", response_model=SongDetailOut)
def get_song(song_id: int, db: Session = Depends(get_session)) -> SongDetailOut:
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    return SongDetailOut(
        id=song.id,
        title=song.title,
        slug=song.slug,
        song_type=song.song_type,
        cover_of=song.cover_of,
        recordings=[_recording_out(r) for r in song.recordings],
    )


@app.get("/api/jams", response_model=list[SongDetailOut])
def list_jams(db: Session = Depends(get_session)) -> list[SongDetailOut]:
    songs = (
        db.query(Song)
        .filter(Song.song_type == "jam")
        .order_by(Song.title)
        .all()
    )
    return [
        SongDetailOut(
            id=s.id,
            title=s.title,
            slug=s.slug,
            song_type=s.song_type,
            cover_of=s.cover_of,
            recordings=[_recording_out(r) for r in s.recordings],
        )
        for s in songs
    ]


@app.get("/api/sessions", response_model=list[SessionOut])
def list_sessions(db: Session = Depends(get_session)) -> list[SessionOut]:
    sessions = db.query(DbSession).order_by(DbSession.date.desc()).all()
    return [
        SessionOut(
            id=s.id,
            date=s.date,
            date_uncertain=s.date_uncertain,
            notes=s.notes,
            recording_count=len(s.recordings),
        )
        for s in sessions
    ]


@app.get("/api/sessions/{session_id}", response_model=SessionDetailOut)
def get_session_detail(
    session_id: int, db: Session = Depends(get_session)
) -> SessionDetailOut:
    session = db.query(DbSession).filter(DbSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionDetailOut(
        id=session.id,
        date=session.date,
        date_uncertain=session.date_uncertain,
        notes=session.notes,
        recordings=[_recording_out(r) for r in session.recordings],
    )


@app.get("/api/recordings/{recording_id}", response_model=RecordingOut)
def get_recording(
    recording_id: int, db: Session = Depends(get_session)
) -> RecordingOut:
    rec = db.query(Recording).filter(Recording.id == recording_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found")
    return _recording_out(rec)


@app.post("/api/recordings/batch-classify")
def batch_classify(
    body: BatchClassifyBody,
    db: Session = Depends(get_session),
) -> dict:
    if body.content_type not in VALID_CONTENT_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid content_type '{body.content_type}'")
    count = 0
    for rid in body.recording_ids:
        rec = db.query(Recording).filter(Recording.id == rid).first()
        if rec:
            rec.content_type = body.content_type
            rec.content_type_source = "human"
            count += 1
    db.commit()
    return {"updated": count}


@app.post("/api/recordings/{recording_id}/classify", response_model=RecordingOut)
def classify_recording(
    recording_id: int,
    body: ClassifyBody,
    db: Session = Depends(get_session),
) -> RecordingOut:
    if body.content_type not in VALID_CONTENT_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid content_type '{body.content_type}'")
    if body.content_type == "song_take" and body.song_id is None:
        raise HTTPException(status_code=422, detail="song_id is required when content_type is 'song_take'")
    rec = db.query(Recording).filter(Recording.id == recording_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found")
    rec.content_type = body.content_type
    rec.content_type_source = "human"
    if body.content_type == "song_take" and body.song_id is not None:
        rec.song_id = body.song_id
        _rename_recording_file(rec, db)
    elif body.content_type == "jam" and rec.song_id is None:
        session_date = rec.session.date if rec.session else None
        title = _jam_auto_title(session_date, db)
        slug = _generate_slug(title, db)
        song = Song(title=title, slug=slug, song_type="jam")
        db.add(song)
        db.flush()
        rec.song_id = song.id
        _rename_recording_file(rec, db)
    db.commit()
    db.refresh(rec)
    return _recording_out(rec)


@app.post("/api/recordings/{recording_id}/assign-song", response_model=RecordingOut)
def assign_song(
    recording_id: int,
    body: AssignSongBody,
    db: Session = Depends(get_session),
) -> RecordingOut:
    rec = db.query(Recording).filter(Recording.id == recording_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found")
    rec.song_id = body.song_id
    rec.content_type = "song_take"
    rec.content_type_source = "human"
    _rename_recording_file(rec, db)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for c in rec.song_match_candidates:
        if c.status == "pending":
            if c.song_id == body.song_id:
                c.status = "accepted"
                c.reviewed_at = now
            else:
                c.status = "rejected"
                c.reviewed_at = now
    db.commit()
    db.refresh(rec)
    return _recording_out(rec)


@app.post("/api/recordings/{recording_id}/unassign-song", response_model=RecordingOut)
def unassign_song(
    recording_id: int,
    db: Session = Depends(get_session),
) -> RecordingOut:
    rec = db.query(Recording).filter(Recording.id == recording_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found")
    rec.song_id = None
    if rec.content_type == "song_take":
        rec.content_type = None
        rec.content_type_source = None
    for c in rec.song_match_candidates:
        c.status = "pending"
        c.reviewed_at = None
    db.commit()
    db.refresh(rec)
    return _recording_out(rec)


@app.get("/api/review/queue", response_model=list[ReviewRecordingOut])
def get_review_queue(
    status: str = "unreviewed",
    sort: str = "confidence",
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_session),
) -> list[ReviewRecordingOut]:
    query = db.query(Recording).filter(
        Recording.song_id.is_(None),
        Recording.origin == "vad_segment",
    )

    if status == "unreviewed":
        query = query.filter(Recording.content_type.is_(None))
    elif status == "auto":
        query = query.filter(Recording.content_type_source == "auto")

    if sort == "confidence":
        BestCandidate = aliased(SongMatchCandidate)
        query = query.outerjoin(
            BestCandidate,
            (BestCandidate.recording_id == Recording.id) & (BestCandidate.rank == 1),
        ).order_by(func.coalesce(BestCandidate.confidence, -1.0).desc())
    elif sort == "duration":
        query = query.order_by(Recording.duration_seconds.asc())
    elif sort == "date":
        query = query.outerjoin(
            DbSession, Recording.session_id == DbSession.id
        ).order_by(DbSession.date.asc())

    recordings = query.offset(offset).limit(limit).all()
    return [_review_recording_out(r, db) for r in recordings]


@app.get("/api/review/stats")
def get_review_stats(db: Session = Depends(get_session)) -> dict:
    total = db.query(func.count(Recording.id)).scalar()
    classified = (
        db.query(func.count(Recording.id))
        .filter(Recording.content_type.is_not(None))
        .scalar()
    )
    unclassified = (
        db.query(func.count(Recording.id))
        .filter(Recording.content_type.is_(None))
        .scalar()
    )
    pending_candidates = (
        db.query(func.count(SongMatchCandidate.id))
        .filter(SongMatchCandidate.status == "pending")
        .scalar()
    )
    by_type_rows = (
        db.query(Recording.content_type, func.count(Recording.id))
        .filter(Recording.content_type.is_not(None))
        .group_by(Recording.content_type)
        .all()
    )
    by_source_rows = (
        db.query(Recording.content_type_source, func.count(Recording.id))
        .filter(Recording.content_type_source.is_not(None))
        .group_by(Recording.content_type_source)
        .all()
    )
    return {
        "total_recordings": total,
        "classified": classified,
        "unclassified": unclassified,
        "pending_candidates": pending_candidates,
        "by_type": {ct: cnt for ct, cnt in by_type_rows},
        "by_source": {src: cnt for src, cnt in by_source_rows},
    }


@app.post("/api/review/candidates/{candidate_id}/accept", response_model=ReviewRecordingOut)
def accept_candidate(
    candidate_id: int, db: Session = Depends(get_session)
) -> ReviewRecordingOut:
    candidate = db.query(SongMatchCandidate).filter(SongMatchCandidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    rec = candidate.recording
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rec.song_id = candidate.song_id
    rec.content_type = "song_take"
    rec.content_type_source = "human"
    candidate.status = "accepted"
    candidate.reviewed_at = now
    for other in rec.song_match_candidates:
        if other.id != candidate_id:
            other.status = "rejected"
            other.reviewed_at = now
    _rename_recording_file(rec, db)
    db.commit()
    db.refresh(rec)
    return _review_recording_out(rec, db)


@app.post("/api/review/candidates/{candidate_id}/reject")
def reject_candidate(
    candidate_id: int, db: Session = Depends(get_session)
) -> list[CandidateOut]:
    candidate = db.query(SongMatchCandidate).filter(SongMatchCandidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    candidate.status = "rejected"
    candidate.reviewed_at = now
    db.commit()
    db.refresh(candidate)
    return [
        _candidate_out(c)
        for c in candidate.recording.song_match_candidates
        if c.status != "rejected"
    ]


@app.post("/api/recordings/{recording_id}/revert", response_model=ReviewRecordingOut)
def revert_recording(
    recording_id: int, db: Session = Depends(get_session)
) -> ReviewRecordingOut:
    rec = db.query(Recording).filter(Recording.id == recording_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found")
    rec.content_type = None
    rec.content_type_source = None
    rec.song_id = None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for c in rec.song_match_candidates:
        c.status = "pending"
        c.reviewed_at = now
    db.commit()
    db.refresh(rec)
    return _review_recording_out(rec, db)


@app.post("/api/recordings/{recording_id}/split", response_model=SplitResult)
def split_recording(
    recording_id: int,
    body: SplitBody,
    db: Session = Depends(get_session),
) -> SplitResult:
    rec = db.query(Recording).filter(Recording.id == recording_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found")
    if not rec.audio_path:
        raise HTTPException(status_code=422, detail="Recording has no audio file")

    audio_file = PROCESSED_ROOT / rec.audio_path
    if not audio_file.exists():
        raise HTTPException(status_code=422, detail="Audio file not found on disk")

    segments = (
        db.query(Segment)
        .filter(Segment.recording_id == recording_id)
        .order_by(Segment.start_seconds)
        .all()
    )
    if not segments:
        raise HTTPException(
            status_code=422,
            detail="Recording has no segments — run the CLAP pipeline first before splitting",
        )

    actual_split_at = min(
        segments, key=lambda s: abs(s.start_seconds - body.split_at)
    ).start_seconds

    duration = rec.duration_seconds or 0.0
    if actual_split_at <= 1.0 or actual_split_at >= duration - 1.0:
        raise HTTPException(
            status_code=422,
            detail=f"Split point {actual_split_at:.1f}s is too close to the start or end",
        )

    out_dir = audio_file.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_a = out_dir / f"_split_tmp_{recording_id}_a.mp3"
    tmp_b = out_dir / f"_split_tmp_{recording_id}_b.mp3"
    final_a: Path | None = None
    final_b: Path | None = None

    try:
        for tmp in (tmp_a, tmp_b):
            if tmp.exists():
                tmp.unlink()

        def _ffmpeg_split(input_path: Path, output_path: Path, ss: float | None, t: float | None) -> None:
            cmd = ["ffmpeg", "-y", "-i", str(input_path)]
            if ss is not None:
                cmd += ["-ss", str(ss)]
            if t is not None:
                cmd += ["-t", str(t)]
            cmd += ["-c", "copy", str(output_path)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg split failed:\n{result.stderr[-2000:]}")

        _ffmpeg_split(audio_file, tmp_a, None, actual_split_at)
        _ffmpeg_split(audio_file, tmp_b, actual_split_at, None)

        orig_start = rec.start_offset_seconds or 0.0
        orig_end = rec.end_offset_seconds or (orig_start + duration)

        rec_a = Recording(
            session_id=rec.session_id,
            origin=rec.origin,
            source_path=rec.source_path,
            start_offset_seconds=orig_start,
            end_offset_seconds=orig_start + actual_split_at,
            duration_seconds=actual_split_at,
            content_type=None,
            content_type_source=None,
        )
        rec_b = Recording(
            session_id=rec.session_id,
            origin=rec.origin,
            source_path=rec.source_path,
            start_offset_seconds=orig_start + actual_split_at,
            end_offset_seconds=orig_end,
            duration_seconds=duration - actual_split_at,
            content_type=None,
            content_type_source=None,
        )
        db.add(rec_a)
        db.add(rec_b)
        db.flush()

        final_a = out_dir / f"{rec_a.id}_split_a.mp3"
        final_b = out_dir / f"{rec_b.id}_split_b.mp3"
        tmp_a.rename(final_a)
        tmp_b.rename(final_b)

        rec_a.audio_path = str(final_a.relative_to(PROCESSED_ROOT))
        rec_b.audio_path = str(final_b.relative_to(PROCESSED_ROOT))

        db.execute(
            update(Segment)
            .where(Segment.recording_id == recording_id, Segment.start_seconds < actual_split_at)
            .values(recording_id=rec_a.id)
        )
        db.execute(
            update(Segment)
            .where(Segment.recording_id == recording_id, Segment.start_seconds >= actual_split_at)
            .values(
                recording_id=rec_b.id,
                start_seconds=Segment.start_seconds - actual_split_at,
                end_seconds=Segment.end_seconds - actual_split_at,
            )
        )

        split_byte = floor_to_4bytes(actual_split_at)
        timeseries_rows = (
            db.query(FeatureTimeseries)
            .filter(FeatureTimeseries.recording_id == recording_id)
            .all()
        )
        for ts in timeseries_rows:
            packed = ts.packed_values
            db.add(FeatureTimeseries(
                recording_id=rec_a.id,
                feature_name=ts.feature_name,
                packed_values=packed[:split_byte],
            ))
            db.add(FeatureTimeseries(
                recording_id=rec_b.id,
                feature_name=ts.feature_name,
                packed_values=packed[split_byte:],
            ))

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for new_id in (rec_a.id, rec_b.id):
            for step in ("ingest", "librosa", "clap"):
                db.add(ProcessingLog(
                    recording_id=new_id,
                    step=step,
                    version="1",
                    completed_at=now,
                    status="success",
                ))

        db.delete(rec)
        db.commit()

        audio_file.unlink(missing_ok=True)

        db.refresh(rec_a)
        db.refresh(rec_b)

        return SplitResult(
            actual_split_at=actual_split_at,
            recordings=[
                _review_recording_out(rec_a, db),
                _review_recording_out(rec_b, db),
            ],
        )

    except Exception:
        db.rollback()
        for f in (tmp_a, tmp_b, final_a, final_b):
            if f and f.exists():
                f.unlink(missing_ok=True)
        raise


def floor_to_4bytes(seconds: float) -> int:
    """Return the byte offset into a packed float32 array at the given second."""
    return int(seconds) * 4


@app.get("/api/segments/{segment_id}/similar", response_model=list[SimilarSegmentOut])
def get_similar_segments(
    segment_id: int,
    k: int = 10,
    db: Session = Depends(get_session),
) -> list[SimilarSegmentOut]:
    seg = db.query(Segment).filter(Segment.id == segment_id).first()
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")
    if seg.clap_embedding is None:
        raise HTTPException(status_code=422, detail="Segment has no CLAP embedding yet")

    index = _get_faiss_index()
    if index is None:
        raise HTTPException(status_code=503, detail="FAISS index not available — run build_faiss_index.py")

    query_vec = unpack_embedding(seg.clap_embedding)
    scores, ids = search(index, query_vec, k=k + 1)

    results: list[SimilarSegmentOut] = []
    for score, sid in zip(scores.tolist(), ids.tolist()):
        if sid == segment_id:
            continue
        match = db.query(Segment).filter(Segment.id == sid).first()
        if match is None:
            continue
        rec = match.recording
        results.append(
            SimilarSegmentOut(
                segment_id=match.id,
                recording_id=match.recording_id,
                start_seconds=match.start_seconds,
                end_seconds=match.end_seconds,
                score=score,
                recording_title=rec.title if rec else None,
                session_date=rec.session.date if rec and rec.session else None,
                audio_path=rec.audio_path if rec else None,
            )
        )
        if len(results) == k:
            break

    return results


@app.get("/api/mood-map/{kind}")
def list_mood_maps(kind: str):
    if kind not in MOOD_MAP_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown kind '{kind}'. Valid kinds: {sorted(MOOD_MAP_KINDS)}",
        )
    index_path = MOOD_MAP_BASE_DIR / kind / "index.json"
    if not index_path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"No mood maps available for kind '{kind}' — run the appropriate build script first.",
        )
    return json.loads(index_path.read_text())


@app.get("/api/mood-map/{kind}/{name}")
def get_mood_map(kind: str, name: str) -> FileResponse:
    if kind not in MOOD_MAP_KINDS:
        raise HTTPException(status_code=400, detail=f"Unknown kind '{kind}'.")
    if not name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid UMAP name.")
    path = MOOD_MAP_BASE_DIR / kind / f"{name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"UMAP '{name}' not found for kind '{kind}'.")
    return FileResponse(str(path), media_type="application/json")


@app.get("/api/passages/runs")
def list_passage_runs():
    if not PASSAGES_BASE_DIR.exists():
        return []
    runs = []
    for d in sorted(PASSAGES_BASE_DIR.iterdir()):
        if not d.is_dir() or not (d / "passages.json").exists():
            continue
        config = json.loads((d / "config.json").read_text()) if (d / "config.json").exists() else {}
        types_data = json.loads((d / "passage_types.json").read_text()) if (d / "passage_types.json").exists() else {}
        passage_count = sum(t.get("count", 0) for t in types_data.values())
        runs.append({
            "name": d.name,
            "n_clusters": config.get("n_clusters", 0),
            "method": config.get("method", "unknown"),
            "passage_count": passage_count,
            "type_count": len(types_data),
        })
    return runs


def _valid_run_name(run: str) -> bool:
    return bool(run) and all(c.isalnum() or c in "-_." for c in run) and ".." not in run


@app.get("/api/passages/{run}/types")
def get_passage_types(run: str):
    if not _valid_run_name(run):
        raise HTTPException(status_code=400, detail="Invalid run name.")
    types_path = PASSAGES_BASE_DIR / run / "passage_types.json"
    if not types_path.exists():
        raise HTTPException(status_code=404, detail=f"No passage types for run '{run}'.")
    return json.loads(types_path.read_text())


@app.get("/api/passages/{run}/type/{type_id}")
def get_passages_by_type(run: str, type_id: int):
    if not _valid_run_name(run):
        raise HTTPException(status_code=400, detail="Invalid run name.")
    passages_path = PASSAGES_BASE_DIR / run / "passages.json"
    if not passages_path.exists():
        raise HTTPException(status_code=404, detail=f"No passages for run '{run}'.")
    passages = json.loads(passages_path.read_text())
    return [p for p in passages if p["passage_type"] == type_id]


@app.get("/api/passages/{run}/recording/{recording_id}")
def get_passages_by_recording(run: str, recording_id: int):
    if not _valid_run_name(run):
        raise HTTPException(status_code=400, detail="Invalid run name.")
    passages_path = PASSAGES_BASE_DIR / run / "passages.json"
    if not passages_path.exists():
        raise HTTPException(status_code=404, detail=f"No passages for run '{run}'.")
    passages = json.loads(passages_path.read_text())
    return sorted(
        [p for p in passages if p["recording_id"] == recording_id],
        key=lambda p: p["start_seconds"],
    )


@app.get("/api/audio/{recording_id}")
def serve_audio(recording_id: int, db: Session = Depends(get_session)) -> FileResponse:
    rec = db.query(Recording).filter(Recording.id == recording_id).first()
    if not rec or not rec.audio_path:
        raise HTTPException(status_code=404, detail="Audio not found")
    audio_file = PROCESSED_ROOT / rec.audio_path
    if not audio_file.exists():
        raise HTTPException(status_code=404, detail="Audio file not on disk")
    return FileResponse(str(audio_file), media_type="audio/mpeg")
