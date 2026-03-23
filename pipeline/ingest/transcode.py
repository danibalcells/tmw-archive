"""FFmpeg transcoding to MP3 320kbps.

Two modes:
  transcode_full    — transcode an entire source file
  transcode_segment — extract a time slice and transcode it

Output filenames use the recording DB primary key as a prefix for stable
lookup, followed by a human-readable label:
  {PROCESSED_ROOT}/recordings/{recording_id}_{label}.mp3

The label is built by the caller (core.py) and kept filesystem-safe.
"""

import logging
import re
import subprocess
import unicodedata
from pathlib import Path

from pipeline.config import PROCESSED_ROOT

log = logging.getLogger(__name__)

MP3_BITRATE = "320k"

_UNSAFE_CHARS_RE = re.compile(r"[^\w\-]")
_MULTI_DASH_RE = re.compile(r"-{2,}")


def build_label(*parts: str | None) -> str:
    """Join non-empty parts with '_', sanitizing each for use in a filename.

    Normalizes unicode (NFD → strip combining marks), replaces spaces and
    unsafe characters with '-', collapses repeated dashes, and trims to
    100 chars total to stay well within filesystem limits.
    """
    sanitized: list[str] = []
    for part in parts:
        if not part:
            continue
        nfkd = unicodedata.normalize("NFD", part)
        ascii_part = nfkd.encode("ascii", "ignore").decode("ascii")
        safe = _UNSAFE_CHARS_RE.sub("-", ascii_part.replace(" ", "-"))
        safe = _MULTI_DASH_RE.sub("-", safe).strip("-")
        if safe:
            sanitized.append(safe)
    label = "_".join(sanitized)
    return label[:100]


def _output_path(recording_id: int, label: str) -> Path:
    filename = f"{recording_id}_{label}.mp3" if label else f"{recording_id}.mp3"
    out = PROCESSED_ROOT / "recordings" / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _run_ffmpeg(cmd: list[str], source_label: str) -> None:
    log.debug("Transcode: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg transcode failed for {source_label}:\n{result.stderr[-2000:]}"
        )


def transcode_full(source: Path, recording_id: int, label: str = "") -> Path:
    """Transcode the entire source file to MP3. Returns the output path."""
    out = _output_path(recording_id, label)
    _run_ffmpeg(
        [
            "ffmpeg", "-y",
            "-i", str(source),
            "-vn",
            "-b:a", MP3_BITRATE,
            str(out),
        ],
        source.name,
    )
    log.info("Transcoded %s → %s", source.name, out.name)
    return out


def transcode_segment(
    source: Path,
    recording_id: int,
    start_seconds: float,
    end_seconds: float,
    label: str = "",
) -> Path:
    """Extract and transcode a time slice from source. Returns the output path."""
    out = _output_path(recording_id, label)
    duration = end_seconds - start_seconds
    _run_ffmpeg(
        [
            "ffmpeg", "-y",
            "-ss", str(start_seconds),
            "-t", str(duration),
            "-i", str(source),
            "-vn",
            "-b:a", MP3_BITRATE,
            str(out),
        ],
        f"{source.name}[{start_seconds:.1f}-{end_seconds:.1f}]",
    )
    log.info(
        "Transcoded %s [%.1f–%.1f] → %s",
        source.name,
        start_seconds,
        end_seconds,
        out.name,
    )
    return out
