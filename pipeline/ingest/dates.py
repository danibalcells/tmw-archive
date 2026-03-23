"""Date parsing utilities for all archive sections.

Each section encodes dates differently:
  - Assajos Pattern A: filename is YYYY-MM-DD.wav
  - Assajos Pattern B: folder name is YYYY-MM-DD [optional descriptor]/
  - Jams: folder name is YYYY-MM-DD/
  - Temas/Covers: filename is SongSlug_YYYY-MM-DD.mp3 (or _Demo, _FullBand, etc.)
  - Old/: folder name is MM%3aYY - DD Month (URL-encoded colon, Catalan month names)
"""

import re
from urllib.parse import unquote

CATALAN_MONTHS: dict[str, int] = {
    "gener": 1,
    "febrer": 2,
    "març": 3,
    "abril": 4,
    "maig": 5,
    "juny": 6,
    "juliol": 7,
    "agost": 8,
    "setembre": 9,
    "octubre": 10,
    "novembre": 11,
    "desembre": 12,
}

_ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_TEMAS_DATE_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})(?:\.|$)")
_OLD_FOLDER_RE = re.compile(
    r"(\d{2})(?:%3a|:)(\d{2})\s*-\s*(\d+)\s+(\w+)", re.IGNORECASE
)


def parse_iso_from_stem(name: str) -> str | None:
    """Extract the first YYYY-MM-DD from a filename stem or folder name."""
    m = _ISO_DATE_RE.search(name)
    return m.group(1) if m else None


def parse_temas_date(filename: str) -> str | None:
    """Extract date from a Temas/Covers filename: SongSlug_YYYY-MM-DD.mp3.

    Returns None for undated variants like SongSlug_Demo.mp3.
    """
    m = _TEMAS_DATE_RE.search(filename)
    return m.group(1) if m else None


def parse_old_folder_date(folder_name: str) -> str | None:
    """Parse Old/ folder names like '12%3a12 - 7 Desembre' → '2012-12-07'.

    Format: MM%3aYY - DD MonthName (URL-encoded colon, Catalan month name).
    Year is 2-digit; Old/ predates 2020, so century prefix is always '20'.
    """
    decoded = unquote(folder_name)
    m = _OLD_FOLDER_RE.match(decoded)
    if not m:
        return None
    mm_str, yy_str, dd_str, month_str = m.groups()
    month_num = CATALAN_MONTHS.get(month_str.lower())
    if not month_num:
        return None
    year = 2000 + int(yy_str)
    return f"{year}-{month_num:02d}-{int(dd_str):02d}"
