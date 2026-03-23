"""Tier subset definitions and recording-level filtering.

TIER1_SOURCE_PATHS and TIER2_SOURCE_PATHS are the canonical lists of archive
paths (or path prefixes) for the Tier 1 and Tier 2 dev subsets. Any pipeline
script that needs to restrict processing to a tier imports from here.

matches_tier() is the shared predicate (prefix-based, same semantics as the
archive scanner). filter_recording_ids_by_tier() applies it against DB rows
for scripts that work on already-ingested recordings rather than IngestItems.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as DBSession

TIER1_SOURCE_PATHS: list[str] = [
    "Assajos/2016-01-17.wav",
    "Assajos/2016-09-22",
    "Jams/2013-11-16/Same-Rain-New-Villages.mp3",
    "Temas/Lady-Monas-Experience/Lady-Monas-Experience_2013-11-16.mp3",
    "Temas/Lady-Monas-Experience/Lady-Monas-Experience_2015-09-25.mp3",
    "Temas/Lady-Monas-Experience/Lady-Monas-Experience_2015-06-12.WAV-split-12.mp3",
    "Temas/Lady-Monas-Experience/Lady-Monas-Experience_Demo.mp3",
    "Old/12%3a12 - 7 Desembre",
]

TIER2_SOURCE_PATHS: list[str] = [
    "Assajos/2016-11-23",
    "Assajos/2016-12-21",
    "Assajos/2016-09-29.WAV",
    "Assajos/2017-03-09.WAV",
    "Jams/2013-09-12/Jammin-In-The-Name-Of.mp3",
    "Jams/2015-10-11/Phryjiam.mp3",
    "Jams/2016-10-05/Gruf Natur.mp3",
    "Jams/2018-09-16/Liquid.wav",
    "Temas/Backstage",
    "Temas/Parabara",
    "Temas/Rain",
]


def tier_paths_for(tiers: list[int]) -> list[str]:
    """Return combined source path prefixes for the given tier numbers (1 and/or 2)."""
    paths: list[str] = []
    for t in tiers:
        if t == 1:
            paths.extend(TIER1_SOURCE_PATHS)
        elif t == 2:
            paths.extend(TIER2_SOURCE_PATHS)
    return paths


def matches_tier(source_paths: list[str], tier_paths: list[str]) -> bool:
    """Return True if any source_path matches any tier_path (prefix or exact)."""
    for tier_path in tier_paths:
        prefix = tier_path.rstrip("/") + "/"
        for sp in source_paths:
            if sp == tier_path or sp.startswith(prefix):
                return True
    return False


def filter_recording_ids_by_tier(
    db: DBSession,
    recording_ids: list[int],
    tier_paths: list[str],
) -> list[int]:
    """Return the subset of recording_ids whose source_path matches tier_paths."""
    from pipeline.db.models import Recording

    result: list[int] = []
    for rec_id in recording_ids:
        rec = db.get(Recording, rec_id)
        if rec is not None and matches_tier(rec.source_path, tier_paths):
            result.append(rec_id)
    return result
