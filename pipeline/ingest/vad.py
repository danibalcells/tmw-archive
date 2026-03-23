"""FFmpeg silence detection (VAD) for raw session recordings.

Runs FFmpeg's silencedetect filter and parses its stderr output to produce
a list of (start_sec, end_sec) non-silent segments.

Default tuning parameters are loaded from pipeline/config/ingest.yaml. Module-level
constants serve as hard fallbacks if no config is found. Expect to iterate
on these against real recordings — the defaults are a reasonable starting
point for room-mic band rehearsals.
"""

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_DEFAULT_SILENCE_THRESHOLD_DB: float = -40.0
_DEFAULT_MIN_SILENCE_DURATION: float = 4.0
_DEFAULT_MIN_SEGMENT_DURATION: float = 40.0

_SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)")
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "ingest.yaml"


def load_ingest_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or _DEFAULT_CONFIG_PATH
    if not path.exists():
        log.debug("No ingest config found at %s; using built-in defaults", path)
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    log.debug("Loaded ingest config from %s", path)
    return data


def _vad_defaults(config: dict[str, Any]) -> tuple[float, float, float]:
    vad = config.get("vad", {})
    return (
        vad.get("silence_threshold_db", _DEFAULT_SILENCE_THRESHOLD_DB),
        vad.get("min_silence_duration", _DEFAULT_MIN_SILENCE_DURATION),
        vad.get("min_segment_duration", _DEFAULT_MIN_SEGMENT_DURATION),
    )


def _get_duration(source: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(source)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def detect_segments(
    source: Path,
    silence_threshold_db: float | None = None,
    min_silence_duration: float | None = None,
    min_segment_duration: float | None = None,
    config: dict[str, Any] | None = None,
) -> list[tuple[float, float]]:
    """Run silencedetect on source and return non-silent (start, end) pairs in seconds.

    Parameters are resolved in priority order: explicit kwargs > config dict > ingest.yaml > built-in defaults.
    Segments shorter than min_segment_duration are discarded.
    """
    cfg = config if config is not None else load_ingest_config()
    cfg_threshold, cfg_min_silence, cfg_min_segment = _vad_defaults(cfg)

    threshold = silence_threshold_db if silence_threshold_db is not None else cfg_threshold
    min_silence = min_silence_duration if min_silence_duration is not None else cfg_min_silence
    min_segment = min_segment_duration if min_segment_duration is not None else cfg_min_segment

    cmd = [
        "ffmpeg",
        "-i", str(source),
        "-af", f"silencedetect=noise={threshold}dB:d={min_silence}",
        "-f", "null",
        "-",
    ]
    log.debug("VAD command: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0 and "silencedetect" not in result.stderr:
        raise RuntimeError(
            f"FFmpeg silencedetect failed for {source}:\n{result.stderr[-2000:]}"
        )

    stderr = result.stderr

    silence_starts: list[float] = [
        float(m.group(1)) for m in _SILENCE_START_RE.finditer(stderr)
    ]
    silence_ends: list[float] = [
        float(m.group(1)) for m in _SILENCE_END_RE.finditer(stderr)
    ]

    try:
        total_duration = _get_duration(source)
    except Exception:
        dm = _DURATION_RE.search(stderr)
        if dm:
            h, m_str, s = dm.groups()
            total_duration = int(h) * 3600 + int(m_str) * 60 + float(s)
        else:
            raise RuntimeError(f"Could not determine duration of {source}")

    boundaries: list[float] = [0.0]
    for end in silence_ends:
        boundaries.append(end)
    for start in silence_starts:
        boundaries.append(start)
    boundaries.append(total_duration)
    boundaries = sorted(set(boundaries))

    segments: list[tuple[float, float]] = []
    for i in range(len(boundaries) - 1):
        seg_start = boundaries[i]
        seg_end = boundaries[i + 1]
        duration = seg_end - seg_start

        is_silent = any(abs(ss - seg_start) < 0.01 for ss in silence_starts)

        if not is_silent and duration >= min_segment:
            segments.append((round(seg_start, 3), round(seg_end, 3)))

    log.info(
        "VAD: %s → %d segments (threshold=%.0fdB, min_silence=%.1fs, min_segment=%.1fs)",
        source.name,
        len(segments),
        threshold,
        min_silence,
        min_segment,
    )
    return segments
