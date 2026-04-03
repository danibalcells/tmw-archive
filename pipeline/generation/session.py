from __future__ import annotations

from typing import Optional

import numpy as np

from .personality import PersonalityConfig
from .selector import PassageSelector, ScoredPassage
from .store import Passage, PassageStore
from .transition import NGramTransitionModel


class PassageSelection:
    def __init__(
        self,
        passage: Passage,
        target_state: int,
        state_distribution: list[float],
        candidates: list[ScoredPassage],
        step: int,
        session_id: str,
        state_history: list[int],
    ) -> None:
        self.passage = passage
        self.target_state = target_state
        self.state_distribution = state_distribution
        self.candidates = candidates
        self.step = step
        self.session_id = session_id
        self.state_history = state_history


class GenerationSession:
    def __init__(
        self,
        session_id: str,
        store: PassageStore,
        model: NGramTransitionModel,
        personality: PersonalityConfig,
        seed: Optional[int] = None,
    ) -> None:
        self._session_id = session_id
        self._store = store
        self._model = model
        self._personality = personality
        self._selector = PassageSelector(store)
        if seed is not None:
            np.random.seed(seed)
        self._step = 0
        self._current: Optional[Passage] = None
        self._state_history: list[int] = []
        self._passage_history: list[int] = []
        self._recording_history: list[int] = []

    def _state_recency_penalty(self) -> np.ndarray:
        penalty = np.ones(self._model.n_states, dtype=np.float64)
        seen: dict[int, int] = {}
        for i, s in enumerate(reversed(self._state_history)):
            if s not in seen:
                seen[s] = i + 1
        halflife = self._personality.recency_halflife
        for state, steps_ago in seen.items():
            if 0 <= state < len(penalty):
                penalty[state] = 1.0 - 0.7 * np.exp(-steps_ago * np.log(2) / max(halflife, 1))
        return penalty

    def next(self) -> PassageSelection:
        if self._model.n_states == 0:
            raise RuntimeError("Transition model has no states — was it trained?")

        penalty = self._state_recency_penalty()
        target_state, state_dist = self._model.sample(
            history=self._state_history,
            temperature=self._personality.temperature,
            recency_penalty=penalty,
        )

        current_bf = (
            self._store.boundary_features(self._current.passage_id)
            if self._current is not None
            else None
        )

        fidelity_id: Optional[int] = None
        if self._current is not None and self._current.successor_id is not None:
            succ = self._store.passage_by_id(self._current.successor_id)
            if succ is not None and succ.passage_type == target_state:
                fidelity_id = succ.passage_id

        passage, candidates = self._selector.select(
            target_state=target_state,
            current_bf=current_bf,
            played_passage_history=self._passage_history,
            played_recording_history=self._recording_history,
            fidelity_id=fidelity_id,
            personality=self._personality,
            n_candidates=8,
        )

        self._current = passage
        self._state_history.append(passage.passage_type)
        self._passage_history.append(passage.passage_id)
        self._recording_history.append(passage.recording_id)
        self._step += 1

        return PassageSelection(
            passage=passage,
            target_state=target_state,
            state_distribution=state_dist.tolist(),
            candidates=candidates,
            step=self._step,
            session_id=self._session_id,
            state_history=list(self._state_history),
        )

    @property
    def current(self) -> Optional[Passage]:
        return self._current

    @property
    def state_history(self) -> list[int]:
        return list(self._state_history)

    @property
    def passage_history(self) -> list[int]:
        return list(self._passage_history)
