from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np


@dataclass
class Passage:
    passage_id: int
    passage_type: int
    recording_id: int
    segment_ids: list[int]
    start_seconds: float
    end_seconds: float
    duration: float
    segment_count: int
    mean_rms: float
    mean_spectral_centroid: float
    recording_title: str
    audio_path: str
    session_date: str
    song_title: Optional[str]
    effective_type: str
    successor_id: Optional[int] = field(default=None, compare=False)


@dataclass
class BoundaryFeatures:
    passage_id: int
    entry_embedding: np.ndarray
    exit_embedding: np.ndarray
    entry_rms: float
    exit_rms: float
    entry_spectral_centroid: float
    exit_spectral_centroid: float


class PassageStore:
    def __init__(
        self,
        run_dir: Path,
        effective_types: Optional[set[str]] = None,
    ) -> None:
        self._run_dir = run_dir
        self._effective_types = effective_types
        self._passages: list[Passage] = []
        self._by_id: dict[int, Passage] = {}
        self._by_state: dict[int, list[Passage]] = {}
        self._boundary_features: dict[int, BoundaryFeatures] = {}
        self._recording_sequences: list[list[Passage]] = []
        self._n_states: int = 0
        self._load()

    def _load(self) -> None:
        raw_passages = json.loads((self._run_dir / "passages.json").read_text())
        all_passages = [
            Passage(
                passage_id=p["passage_id"],
                passage_type=p["passage_type"],
                recording_id=p["recording_id"],
                segment_ids=p["segment_ids"],
                start_seconds=p["start_seconds"],
                end_seconds=p["end_seconds"],
                duration=p["duration"],
                segment_count=p["segment_count"],
                mean_rms=float(p.get("mean_rms") or 0.0),
                mean_spectral_centroid=float(p.get("mean_spectral_centroid") or 0.0),
                recording_title=p.get("recording_title") or "",
                audio_path=p.get("audio_path") or "",
                session_date=p.get("session_date") or "",
                song_title=p.get("song_title"),
                effective_type=p.get("effective_type") or "",
            )
            for p in raw_passages
        ]

        if self._effective_types:
            self._passages = [p for p in all_passages if p.effective_type in self._effective_types]
        else:
            self._passages = all_passages

        included_ids = {p.passage_id for p in self._passages}
        self._by_id = {p.passage_id: p for p in self._passages}

        state_ids = sorted({p.passage_type for p in self._passages})
        self._n_states = (max(state_ids) + 1) if state_ids else 0
        self._by_state = {s: [] for s in range(self._n_states)}
        for p in self._passages:
            self._by_state[p.passage_type].append(p)

        bf_path = self._run_dir / "boundary_features.json"
        if bf_path.exists():
            for b in json.loads(bf_path.read_text()):
                if b["passage_id"] not in included_ids:
                    continue
                self._boundary_features[b["passage_id"]] = BoundaryFeatures(
                    passage_id=b["passage_id"],
                    entry_embedding=np.array(b.get("entry_embedding") or [], dtype=np.float32),
                    exit_embedding=np.array(b.get("exit_embedding") or [], dtype=np.float32),
                    entry_rms=float(b.get("entry_rms") or 0.0),
                    exit_rms=float(b.get("exit_rms") or 0.0),
                    entry_spectral_centroid=float(b.get("entry_spectral_centroid") or 0.0),
                    exit_spectral_centroid=float(b.get("exit_spectral_centroid") or 0.0),
                )

        by_recording: dict[int, list[Passage]] = {}
        for p in self._passages:
            by_recording.setdefault(p.recording_id, []).append(p)

        for rec_passages in by_recording.values():
            rec_passages.sort(key=lambda x: x.start_seconds)
            for i, p in enumerate(rec_passages[:-1]):
                p.successor_id = rec_passages[i + 1].passage_id
            for seqs in self._split_into_contiguous(rec_passages):
                self._recording_sequences.append(seqs)

    @staticmethod
    def _split_into_contiguous(passages: list[Passage]) -> list[list[Passage]]:
        if not passages:
            return []
        seqs: list[list[Passage]] = []
        current: list[Passage] = [passages[0]]
        for p in passages[1:]:
            if p.start_seconds - current[-1].end_seconds > 60:
                if len(current) >= 2:
                    seqs.append(current)
                current = [p]
            else:
                current.append(p)
        if len(current) >= 2:
            seqs.append(current)
        return seqs

    @property
    def n_states(self) -> int:
        return self._n_states

    def all_passages(self) -> list[Passage]:
        return self._passages

    def passages_by_state(self, state_id: int) -> list[Passage]:
        return self._by_state.get(state_id, [])

    def passage_by_id(self, passage_id: int) -> Optional[Passage]:
        return self._by_id.get(passage_id)

    def boundary_features(self, passage_id: int) -> Optional[BoundaryFeatures]:
        return self._boundary_features.get(passage_id)

    def recording_sequences(self) -> list[list[Passage]]:
        return self._recording_sequences
