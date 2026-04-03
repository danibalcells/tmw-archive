from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .personality import PersonalityConfig
from .store import BoundaryFeatures, Passage, PassageStore


@dataclass
class ScoredPassage:
    passage: Passage
    score: float
    boundary_similarity: float
    recency_factor: float
    fidelity_bonus: float
    energy_continuity: float


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-8 or nb < 1e-8:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _recency_factor(steps_since: Optional[int], halflife: int) -> float:
    if steps_since is None:
        return 1.0
    return float(1.0 - 0.9 * np.exp(-steps_since * np.log(2) / max(halflife, 1)))


class PassageSelector:
    def __init__(self, store: PassageStore) -> None:
        self._store = store

    def score_passages(
        self,
        candidates: list[Passage],
        current_bf: Optional[BoundaryFeatures],
        played_passage_history: list[int],
        played_recording_history: list[int],
        fidelity_id: Optional[int],
        personality: PersonalityConfig,
    ) -> list[ScoredPassage]:
        passage_recency: dict[int, int] = {}
        for i, pid in enumerate(reversed(played_passage_history)):
            if pid not in passage_recency:
                passage_recency[pid] = i + 1

        recording_recency: dict[int, int] = {}
        for i, rid in enumerate(reversed(played_recording_history)):
            if rid not in recording_recency:
                recording_recency[rid] = i + 1

        scored: list[ScoredPassage] = []
        for passage in candidates:
            bf = self._store.boundary_features(passage.passage_id)

            if (
                current_bf is not None
                and bf is not None
                and len(current_bf.exit_embedding) > 0
                and len(bf.entry_embedding) > 0
            ):
                raw_sim = _cosine_sim(current_bf.exit_embedding, bf.entry_embedding)
                boundary_sim = (raw_sim + 1.0) / 2.0
            else:
                boundary_sim = 0.5

            p_recency = _recency_factor(
                passage_recency.get(passage.passage_id),
                personality.recency_halflife,
            )
            r_recency = _recency_factor(
                recording_recency.get(passage.recording_id),
                personality.recording_recency_halflife,
            )
            recency = p_recency * r_recency

            fidelity_bonus = personality.fidelity_weight if passage.passage_id == fidelity_id else 0.0

            if current_bf is not None and bf is not None:
                rms_delta = abs(current_bf.exit_rms - bf.entry_rms)
                max_rms = max(current_bf.exit_rms, bf.entry_rms, 1e-6)
                energy_continuity = float(1.0 - min(rms_delta / max_rms, 1.0))
            else:
                energy_continuity = 0.5

            base = (
                personality.boundary_weight * boundary_sim
                + fidelity_bonus
                + personality.energy_weight * energy_continuity
            )
            score = base * recency

            scored.append(
                ScoredPassage(
                    passage=passage,
                    score=score,
                    boundary_similarity=boundary_sim,
                    recency_factor=recency,
                    fidelity_bonus=fidelity_bonus,
                    energy_continuity=energy_continuity,
                )
            )

        return scored

    def select(
        self,
        target_state: int,
        current_bf: Optional[BoundaryFeatures],
        played_passage_history: list[int],
        played_recording_history: list[int],
        fidelity_id: Optional[int],
        personality: PersonalityConfig,
        n_candidates: int = 8,
    ) -> tuple[Passage, list[ScoredPassage]]:
        candidates = self._store.passages_by_state(target_state)
        if not candidates:
            candidates = [random.choice(self._store.all_passages())]

        scored = self.score_passages(
            candidates=candidates,
            current_bf=current_bf,
            played_passage_history=played_passage_history,
            played_recording_history=played_recording_history,
            fidelity_id=fidelity_id,
            personality=personality,
        )

        scores = np.array([s.score for s in scored], dtype=np.float64)
        scores = np.maximum(scores, 1e-10)

        if personality.temperature != 1.0:
            log_scores = np.log(scores) / personality.temperature
            log_scores -= log_scores.max()
            scores = np.exp(log_scores)

        probs = scores / scores.sum()
        chosen_idx = int(np.random.choice(len(scored), p=probs))
        top_candidates = sorted(scored, key=lambda x: -x.score)[:n_candidates]

        return scored[chosen_idx].passage, top_candidates
