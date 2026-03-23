"""Archive scanner: walks archive sections and yields IngestItems.

Each IngestItem describes one logical unit of audio to be ingested:
- source_paths: one or more archive-relative paths (multiple = FAT32-split pair)
- origin: 'pretrimmed' (already a take) or 'raw' (needs VAD segmentation)
- date / date_uncertain: parsed from the filename/folder
- song_slug / song_type / title: populated for sections with song identity

Sections and their origins:
  Assajos/   → raw (VAD needed)
  Jams/      → pretrimmed, song_type='jam'
  Temas/     → pretrimmed, song_type='original'
  Covers/    → pretrimmed, song_type='cover'
  Old/       → pretrimmed, song_type=None (song_id left null)

FAT32-split pairs (_LR-0001 / _LR-0002) are detected but emitted as a warning
and skipped for Tier 1. They will be handled in Tier 2.

Excluded sections: chrysler/, routine/, sanctum/, tm9/, tm10/, Live at the patio/
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from pipeline.ingest.dates import (
    parse_iso_from_stem,
    parse_old_folder_date,
    parse_temas_date,
)

log = logging.getLogger(__name__)

AUDIO_SUFFIXES: frozenset[str] = frozenset({".mp3", ".wav", ".aif", ".aiff", ".m4a"})
IGNORED_SUFFIXES: frozenset[str] = frozenset({".hprj", ".mp4", ".pdf", ".txt"})

_FAT32_SPLIT_RE = re.compile(r"_LR-\d{4}\.", re.IGNORECASE)
_LR_FILE_RE = re.compile(r"_LR\.", re.IGNORECASE)

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


@dataclass
class IngestItem:
    section: str
    source_paths: list[str]
    origin: str
    date: str | None = None
    date_uncertain: bool = False
    song_slug: str | None = None
    song_type: str | None = None
    title: str | None = None
    notes: str | None = None
    extra: dict = field(default_factory=dict)


def _is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_SUFFIXES


def _rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def _humanize(slug: str) -> str:
    from urllib.parse import unquote as _unquote
    return re.sub(r"[-_]+", " ", _unquote(slug)).title()


def _scan_assajos(root: Path) -> list[IngestItem]:
    """Yield one IngestItem per logical Assajos session.

    Pattern A: YYYY-MM-DD[...].WAV  — single file
    Pattern B: YYYY-MM-DD[...]/     — folder; pick *_LR.WAV, skip _Tr* stems
    """
    items: list[IngestItem] = []
    section_root = root / "Assajos"
    if not section_root.exists():
        return items

    for entry in sorted(section_root.iterdir()):
        if entry.is_file():
            if not _is_audio(entry):
                continue
            date = parse_iso_from_stem(entry.stem)
            if not date:
                log.warning("Assajos: cannot parse date from file %s — skipping", entry.name)
                continue
            items.append(IngestItem(
                section="assajos",
                source_paths=[_rel(entry, root)],
                origin="raw",
                date=date,
            ))

        elif entry.is_dir():
            date = parse_iso_from_stem(entry.name)
            if not date:
                log.warning("Assajos: cannot parse date from folder %s — skipping", entry.name)
                continue

            notes_match = re.search(r"\d{4}-\d{2}-\d{2}\s+(.+)", entry.name)
            notes = notes_match.group(1).strip() if notes_match else None

            lr_files = sorted(f for f in entry.rglob("*") if _is_audio(f) and _LR_FILE_RE.search(f.name))
            fat32_pairs: dict[str, list[Path]] = {}
            plain_lr: list[Path] = []

            for f in lr_files:
                if _FAT32_SPLIT_RE.search(f.name):
                    key = re.sub(r"-\d{4}\.", ".", f.name)
                    fat32_pairs.setdefault(key, []).append(f)
                else:
                    plain_lr.append(f)

            for key, parts in fat32_pairs.items():
                log.warning(
                    "Assajos: FAT32-split pair detected in %s (%s) — deferred to Tier 2",
                    entry.name,
                    key,
                )

            for lr in plain_lr:
                items.append(IngestItem(
                    section="assajos",
                    source_paths=[_rel(lr, root)],
                    origin="raw",
                    date=date,
                    notes=notes,
                ))

    return items


def _scan_pretrimmed_flat(root: Path, section_dir: str, song_type: str) -> list[IngestItem]:
    """Scan Jams/: YYYY-MM-DD/ folders containing pretrimmed files."""
    items: list[IngestItem] = []
    section_root = root / section_dir
    if not section_root.exists():
        return items

    for date_folder in sorted(section_root.iterdir()):
        if not date_folder.is_dir():
            continue

        date = parse_iso_from_stem(date_folder.name)
        date_uncertain = date == "2015-01-01"
        if not date:
            log.warning("%s: cannot parse date from folder %s — skipping", section_dir, date_folder.name)
            continue

        for f in sorted(date_folder.iterdir()):
            if not _is_audio(f):
                continue
            raw_slug = f.stem
            slug = unquote(raw_slug)
            items.append(IngestItem(
                section=section_dir.lower(),
                source_paths=[_rel(f, root)],
                origin="pretrimmed",
                date=date,
                date_uncertain=date_uncertain,
                song_slug=slug,
                song_type=song_type,
                title=_humanize(slug),
            ))

    return items


def _scan_song_folders(root: Path, section_dir: str, song_type: str) -> list[IngestItem]:
    """Scan Temas/ and Covers/: SongSlug/ folders containing dated takes."""
    items: list[IngestItem] = []
    section_root = root / section_dir
    if not section_root.exists():
        return items

    for song_folder in sorted(section_root.iterdir()):
        if not song_folder.is_dir():
            continue
        song_slug = song_folder.name

        for f in sorted(song_folder.iterdir()):
            if not _is_audio(f):
                continue
            date = parse_temas_date(f.name)
            items.append(IngestItem(
                section=section_dir.lower(),
                source_paths=[_rel(f, root)],
                origin="pretrimmed",
                date=date,
                song_slug=song_slug,
                song_type=song_type,
                title=_humanize(song_slug),
            ))

    return items


def _scan_old(root: Path) -> list[IngestItem]:
    """Scan Old/: URL-encoded folders with Catalan month names."""
    items: list[IngestItem] = []
    section_root = root / "Old"
    if not section_root.exists():
        return items

    for date_folder in sorted(section_root.iterdir()):
        if not date_folder.is_dir():
            continue
        date = parse_old_folder_date(date_folder.name)
        if not date:
            log.warning("Old: cannot parse date from folder %s — skipping", date_folder.name)
            continue

        for f in sorted(date_folder.iterdir()):
            if not _is_audio(f):
                continue
            title = unquote(f.stem)
            items.append(IngestItem(
                section="old",
                source_paths=[_rel(f, root)],
                origin="pretrimmed",
                date=date,
                song_slug=None,
                song_type=None,
                title=title,
            ))

    return items


def scan_archive(root: Path, tier1_only: bool = False, tier2_only: bool = False) -> list[IngestItem]:
    """Walk the full archive and return all IngestItems.

    When tier1_only or tier2_only is True, only the paths listed in the
    corresponding TIER*_SOURCE_PATHS are returned. The match is prefix-based:
    a paths entry that is a directory matches all items whose source_path starts
    with that prefix.
    """
    all_items: list[IngestItem] = (
        _scan_assajos(root)
        + _scan_pretrimmed_flat(root, "Jams", "jam")
        + _scan_song_folders(root, "Temas", "original")
        + _scan_song_folders(root, "Covers", "cover")
        + _scan_old(root)
    )

    if not tier1_only and not tier2_only:
        return all_items

    tier_paths = TIER1_SOURCE_PATHS if tier1_only else TIER2_SOURCE_PATHS
    tier_label = "Tier 1" if tier1_only else "Tier 2"

    def _matches(item: IngestItem) -> bool:
        for tier_path in tier_paths:
            for sp in item.source_paths:
                if sp == tier_path or sp.startswith(tier_path.rstrip("/") + "/"):
                    return True
        return False

    filtered = [item for item in all_items if _matches(item)]
    log.info("%s filter: %d / %d items selected", tier_label, len(filtered), len(all_items))
    return filtered
