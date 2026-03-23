from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
    tracks: Mapped[list["Track"]] = relationship(back_populates="session")


class Recording(Base):
    """One logical audio capture. May span multiple physical files (FAT32 split pairs).

    source_paths: JSON list of paths relative to ARCHIVE_ROOT.
        Single file: ["Jams/2014-11-03/Blades.mp3"]
        FAT32 split: ["Assajos/2016-07-14/ZOOM0004_LR-0001.WAV",
                       "Assajos/2016-07-14/ZOOM0004_LR-0002.WAV"]
    kind:
        'raw_session'  — full-session WAV requiring VAD segmentation (Assajos).
        'pretrimmed'   — already-trimmed file that maps 1:1 to a Track.
    is_primary: False for Assajos Tr1–4 instrument stems; excluded from the
        current pipeline but retained for potential future use.
    duration_seconds: NULL until the audio analysis step runs.
    """

    __tablename__ = "recordings"
    __table_args__ = (
        CheckConstraint("format IN ('mp3', 'wav', 'aif')", name="ck_recordings_format"),
        CheckConstraint("kind IN ('raw_session', 'pretrimmed')", name="ck_recordings_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sessions.id"), index=True)
    source_paths: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    format: Mapped[str] = mapped_column(String(4), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float)

    session: Mapped[Optional["Session"]] = relationship(back_populates="recordings")
    tracks: Mapped[list["Track"]] = relationship(back_populates="recording")


class Song(Base):
    """One composition, covering both originals and covers.

    slug: hyphenated folder name used in Temas/ and Covers/ (e.g. 'Rain',
        'Under-The-Bridge').
    cover_of: original artist name if this is a cover, None for originals.
    """

    __tablename__ = "songs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    cover_of: Mapped[Optional[str]] = mapped_column(Text)

    tracks: Mapped[list["Track"]] = relationship(back_populates="song")


class Track(Base):
    """Primary browsing atom — one musical segment.

    For raw sessions (Assajos): produced by VAD; start/end offsets are set
        relative to the parent recording file.
    For pretrimmed files (Jams/Temas/Covers/Old): the file IS the track;
        start/end offsets are None.

    session_id: denormalized from recording.session_id for query convenience
        (avoids a join on every listing or filter). Static data — no drift risk.

    kind:
        'vad_segment'  — produced by VAD from a raw session recording.
        'pretrimmed'   — file is already trimmed, ingested directly.
        'live'         — from the Live at the patio session.
    """

    __tablename__ = "tracks"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('vad_segment', 'pretrimmed', 'live')", name="ck_tracks_kind"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recordings.id"), nullable=False, index=True
    )
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sessions.id"), index=True)
    song_id: Mapped[Optional[int]] = mapped_column(ForeignKey("songs.id"), index=True)
    title: Mapped[Optional[str]] = mapped_column(Text)
    start_offset_seconds: Mapped[Optional[float]] = mapped_column(Float)
    end_offset_seconds: Mapped[Optional[float]] = mapped_column(Float)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    recording: Mapped["Recording"] = relationship(back_populates="tracks")
    session: Mapped[Optional["Session"]] = relationship(back_populates="tracks")
    song: Mapped[Optional["Song"]] = relationship(back_populates="tracks")
    segments: Mapped[list["Segment"]] = relationship(back_populates="track")
    feature_timeseries: Mapped[list["FeatureTimeseries"]] = relationship(back_populates="track")


class Segment(Base):
    """30-second window within a track; the engine of smart features.

    clap_embedding: 512-dim float32 vector stored as raw bytes; None until the
        CLAP pipeline step runs. The FAISS index is a sidecar file, not here.
    """

    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id"), nullable=False, index=True
    )
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    clap_embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary)

    track: Mapped["Track"] = relationship(back_populates="segments")


class FeatureTimeseries(Base):
    """Dense per-second scalar curve for a track and named feature.

    packed_values: packed little-endian float32 array, one value per second.
        Stored as raw bytes — fetched wholesale by (track, feature), never
        queried by individual value.
    """

    __tablename__ = "feature_timeseries"
    __table_args__ = (UniqueConstraint("track_id", "feature_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id"), nullable=False, index=True
    )
    feature_name: Mapped[str] = mapped_column(String(64), nullable=False)
    packed_values: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    track: Mapped["Track"] = relationship(back_populates="feature_timeseries")
