from __future__ import annotations

from collections import defaultdict
from typing import Optional

import numpy as np


class NGramTransitionModel:
    def __init__(self, max_order: int = 3, smoothing_k: float = 5.0) -> None:
        self.max_order = max_order
        self.smoothing_k = smoothing_k
        self.n_states: int = 0
        self._ngram_counts: dict[int, dict[tuple, np.ndarray]] = {}

    def train(self, sequences: list[list[int]], n_states: int) -> None:
        self.n_states = n_states
        raw: dict[int, defaultdict] = {
            o: defaultdict(lambda n=n_states: np.zeros(n, dtype=np.float64))
            for o in range(self.max_order + 1)
        }
        for seq in sequences:
            for i, state in enumerate(seq):
                raw[0][()][state] += 1.0
                for order in range(1, self.max_order + 1):
                    if i >= order:
                        context = tuple(seq[i - order : i])
                        raw[order][context][state] += 1.0
        self._ngram_counts = {o: dict(d) for o, d in raw.items()}

    def predict(self, history: list[int]) -> np.ndarray:
        unigram_raw = self._ngram_counts.get(0, {}).get((), np.ones(self.n_states))
        probs = (unigram_raw + 1.0) / (unigram_raw.sum() + self.n_states)

        for order in range(1, min(self.max_order, len(history)) + 1):
            context = tuple(history[-order:])
            counts = self._ngram_counts.get(order, {}).get(context)
            if counts is None:
                continue
            total = counts.sum()
            if total == 0.0:
                continue
            lam = total / (total + self.smoothing_k)
            probs = (1.0 - lam) * probs + lam * (counts / total)

        s = probs.sum()
        return probs / s if s > 0.0 else np.ones(self.n_states) / self.n_states

    def sample(
        self,
        history: list[int],
        temperature: float = 1.0,
        recency_penalty: Optional[np.ndarray] = None,
        personality_bias: Optional[np.ndarray] = None,
        alpha: float = 0.0,
    ) -> tuple[int, np.ndarray]:
        probs = self.predict(history)

        if personality_bias is not None and alpha > 0.0:
            probs = (1.0 - alpha) * probs + alpha * personality_bias
            probs = probs / probs.sum()

        if recency_penalty is not None:
            probs = probs * recency_penalty
            probs = np.maximum(probs, 1e-10)
            probs = probs / probs.sum()

        if temperature != 1.0:
            log_probs = np.log(probs + 1e-10) / temperature
            log_probs -= log_probs.max()
            probs = np.exp(log_probs)
            probs = probs / probs.sum()

        state = int(np.random.choice(self.n_states, p=probs))
        return state, probs
