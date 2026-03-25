from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

CONTENT_TYPE_VALUES = (
    "'song_take'", "'jam'", "'banter'", "'tuning'", "'noodling'",
    "'silence'", "'count_in'", "'other'",
)
CONTENT_TYPE_SOURCE_VALUES = ("'ingest'", "'auto'", "'human'")


class Base(DeclarativeBase):
    pass


class Session(Base):
    """One rehearsal day, inferred from date across all archive sections.

    date: ISO 8601 YYYY-MM-DD. Sentinel '2012-01-01' marks unknown/uncertain
        dates (predates any real content — archive begins Dec 2012).
    date_uncertain: True when the date is a placeholder (e.g. the three files
        in Jams/2015-01-01/, or Live at the patio which has no encoded date).
    notes: free-text annotations such as guest musicians ("ft manaswi").
    """

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String(10), nullable=False, default="2012-01-01")
    date_uncertain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    recordings: Mapped[list["Recording"]] = relationship(back_populates="session")


class Song(Base):
    """One abstract composition — the intellectual property, not a specific capture.

    song_type ('type' in DB): 'original' | 'cover' | 'jam'
    slug: hyphenated folder name used in Temas/ and Covers/ (e.g. 'Rain',
        'Under-The-Bridge'). For jams, slugified from the filename.
    cover_of: original artist name if song_type='cover', None otherwise.
    """

    __tablename__ = "songs"
    __table_args__ = (
        CheckConstraint(
            "type IN ('original', 'cover', 'jam')", name="ck_songs_type"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    song_type: Mapped[str] = mapped_column("type", String(16), nullable=False)
    cover_of: Mapped[Optional[str]] = mapped_column(Text)

    recordings: Mapped[list["Recording"]] = relationship(back_populates="song")


class Recording(Base):
    """One captured performance — the atom that gets stored and served.

    Replaces the former separate Track + Recording pair. A recording is one
    specific performance, tied to a Session and optionally to a Song.

    source_path: JSON list of archive paths relative to ARCHIVE_ROOT. Usually
        a single element; two elements for FAT32-split Zoom pairs that must be
        concatenated before VAD (e.g. LR-0001.WAV + LR-0002.WAV).
    audio_path: path to the processed MP3 output relative to PROCESSED_ROOT.
        Null until the pipeline writes it. This is what the browser serves.
    start_offset_seconds / end_offset_seconds: non-null only for vad_segment
        recordings; marks where this segment falls within the source WAV.
    origin:
        'pretrimmed'  — file is already a trimmed take, ingested directly.
        'vad_segment' — extracted from a raw session WAV via silence detection.
    """

    __tablename__ = "recordings"
    __table_args__ = (
        CheckConstraint(
            "origin IN ('pretrimmed', 'vad_segment')", name="ck_recordings_origin"
        ),
        CheckConstraint(
            f"content_type IN ({', '.join(CONTENT_TYPE_VALUES)})",
            name="ck_recordings_content_type",
        ),
        CheckConstraint(
            f"content_type_source IN ({', '.join(CONTENT_TYPE_SOURCE_VALUES)})",
            name="ck_recordings_content_type_source",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sessions.id"), nullable=True, index=True
    )
    song_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("songs.id"), nullable=True, index=True
    )
    title: Mapped[Optional[str]] = mapped_column(Text)
    source_path: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    audio_path: Mapped[Optional[str]] = mapped_column(Text)
    start_offset_seconds: Mapped[Optional[float]] = mapped_column(Float)
    end_offset_seconds: Mapped[Optional[float]] = mapped_column(Float)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    coverhunter_embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    content_type: Mapped[Optional[str]] = mapped_column(String(16))
    content_type_source: Mapped[Optional[str]] = mapped_column(String(16))

    session: Mapped[Optional["Session"]] = relationship(back_populates="recordings")
    song: Mapped[Optional["Song"]] = relationship(back_populates="recordings")
    segments: Mapped[list["Segment"]] = relationship(
        back_populates="recording", cascade="all, delete-orphan"
    )
    feature_timeseries: Mapped[list["FeatureTimeseries"]] = relationship(
        back_populates="recording", cascade="all, delete-orphan"
    )
    processing_logs: Mapped[list["ProcessingLog"]] = relationship(
        back_populates="recording", cascade="all, delete-orphan"
    )
    song_match_candidates: Mapped[list["SongMatchCandidate"]] = relationship(
        back_populates="recording",
        foreign_keys="SongMatchCandidate.recording_id",
        cascade="all, delete-orphan",
    )


class Segment(Base):
    """20-second window with 50% overlap within a recording; the engine of smart features.

    clap_embedding: 512-dim float32 vector stored as raw bytes; None until the
        CLAP pipeline step runs. The FAISS index is a sidecar file, not here.
    mean_chroma / var_chroma: 12-dim float32 vectors stored as raw bytes (48 bytes each).
        Populated by the librosa pipeline step alongside the scalar stat columns.
    """

    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recordings.id"), nullable=False, index=True
    )
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    clap_embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    mean_rms: Mapped[Optional[float]] = mapped_column(Float)
    var_rms: Mapped[Optional[float]] = mapped_column(Float)
    mean_spectral_centroid: Mapped[Optional[float]] = mapped_column(Float)
    var_spectral_centroid: Mapped[Optional[float]] = mapped_column(Float)
    mean_chroma: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    var_chroma: Mapped[Optional[bytes]] = mapped_column(LargeBinary)

    recording: Mapped["Recording"] = relationship(back_populates="segments")


class FeatureTimeseries(Base):
    """Dense per-second scalar curve for a recording and named feature.

    packed_values: packed little-endian float32 array, one value per second.
        Stored as raw bytes — fetched wholesale by (recording, feature), never
        queried by individual value.
    """

    __tablename__ = "feature_timeseries"
    __table_args__ = (UniqueConstraint("recording_id", "feature_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recordings.id"), nullable=False, index=True
    )
    feature_name: Mapped[str] = mapped_column(String(64), nullable=False)
    packed_values: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    recording: Mapped["Recording"] = relationship(back_populates="feature_timeseries")


class ProcessingLog(Base):
    """Append-only log of pipeline steps applied to a recording.

    step: which pipeline step ran — one of the checked values below.
    version: opaque string identifying the step's code version (e.g. "1").
        Used to re-run steps after logic changes without re-processing already-
        current recordings.
    completed_at: UTC timestamp when the step finished (success or failure).
    status: outcome — 'success', 'failed', or 'skipped'.
    error_message: populated on 'failed'; None otherwise.

    The table is append-only. Retrying a failed step produces a new row.
    needs_processing() queries for the absence of any success row.
    """

    __tablename__ = "processing_log"
    __table_args__ = (
        CheckConstraint(
            "step IN ('ingest', 'librosa', 'clap', 'coverhunter')",
            name="ck_processing_log_step",
        ),
        CheckConstraint(
            "status IN ('success', 'failed', 'skipped')",
            name="ck_processing_log_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recordings.id"), nullable=False, index=True
    )
    step: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    recording: Mapped["Recording"] = relationship(back_populates="processing_logs")


class SongMatchCandidate(Base):
    """Proposed song match for an unlabeled recording, produced by match_songs.py.

    recording_id: the unknown recording being identified.
    song_id: the proposed song match.
    nearest_recording_id: which specific labeled recording had the highest
        cosine similarity — stored so the QA UI can play it side-by-side.
    confidence: cosine similarity score (0–1); best per-song similarity across
        all labeled reference recordings.
    rank: 1 = best match for this recording, 2 = second best, etc.
    status: 'pending' | 'accepted' | 'rejected'
    """

    __tablename__ = "song_match_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name="ck_song_match_candidates_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recordings.id"), nullable=False, index=True
    )
    song_id: Mapped[int] = mapped_column(
        ForeignKey("songs.id"), nullable=False, index=True
    )
    nearest_recording_id: Mapped[int] = mapped_column(
        ForeignKey("recordings.id"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    recording: Mapped["Recording"] = relationship(
        back_populates="song_match_candidates",
        foreign_keys=[recording_id],
    )
    song: Mapped["Song"] = relationship(foreign_keys=[song_id])
    nearest_recording: Mapped["Recording"] = relationship(foreign_keys=[nearest_recording_id])
