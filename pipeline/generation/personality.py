from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PersonalityConfig:
    name: str
    temperature: float = 1.0
    alpha: float = 0.0
    fidelity_weight: float = 0.2
    boundary_weight: float = 0.5
    energy_weight: float = 0.2
    recency_halflife: int = 8
    recording_recency_halflife: int = 16


PRESETS: dict[str, PersonalityConfig] = {
    "faithful": PersonalityConfig(
        name="faithful",
        temperature=0.7,
        fidelity_weight=0.6,
        boundary_weight=0.3,
        energy_weight=0.1,
        recency_halflife=12,
        recording_recency_halflife=24,
    ),
    "explorer": PersonalityConfig(
        name="explorer",
        temperature=1.8,
        fidelity_weight=0.0,
        boundary_weight=0.3,
        energy_weight=0.1,
        recency_halflife=4,
        recording_recency_halflife=8,
    ),
    "chill": PersonalityConfig(
        name="chill",
        temperature=0.9,
        fidelity_weight=0.2,
        boundary_weight=0.6,
        energy_weight=0.3,
        recency_halflife=8,
        recording_recency_halflife=16,
    ),
}


def get_preset(name: str) -> PersonalityConfig:
    if name not in PRESETS:
        raise ValueError(f"Unknown personality '{name}'. Choose from: {list(PRESETS.keys())}")
    return PRESETS[name]
