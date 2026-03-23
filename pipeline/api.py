from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pipeline.config import PROCESSED_ROOT
from pipeline.db.models import Recording, Session as DbSession, Song
from pipeline.db.session import get_session

app = FastAPI(title="TMW Archive API")

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


@app.get("/api/audio/{recording_id}")
def serve_audio(recording_id: int, db: Session = Depends(get_session)) -> FileResponse:
    rec = db.query(Recording).filter(Recording.id == recording_id).first()
    if not rec or not rec.audio_path:
        raise HTTPException(status_code=404, detail="Audio not found")
    audio_file = PROCESSED_ROOT / rec.audio_path
    if not audio_file.exists():
        raise HTTPException(status_code=404, detail="Audio file not on disk")
    return FileResponse(str(audio_file), media_type="audio/mpeg")
