from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pipeline.config import PROCESSED_ROOT
from pipeline.db.models import Recording, Segment, Session as DbSession, Song
from pipeline.db.session import get_session
from pipeline.features.clap_embeddings import unpack_embedding
from pipeline.features.faiss_index import DEFAULT_INDEX_PATH, load_index, search

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
    allow_methods=["GET"],
    allow_headers=["*"],
)


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


class SimilarSegmentOut(BaseModel):
    segment_id: int
    recording_id: int
    start_seconds: float
    end_seconds: float
    score: float
    recording_title: Optional[str]
    session_date: Optional[str]
    audio_path: Optional[str]


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


@app.get("/api/audio/{recording_id}")
def serve_audio(recording_id: int, db: Session = Depends(get_session)) -> FileResponse:
    rec = db.query(Recording).filter(Recording.id == recording_id).first()
    if not rec or not rec.audio_path:
        raise HTTPException(status_code=404, detail="Audio not found")
    audio_file = PROCESSED_ROOT / rec.audio_path
    if not audio_file.exists():
        raise HTTPException(status_code=404, detail="Audio file not on disk")
    return FileResponse(str(audio_file), media_type="audio/mpeg")
