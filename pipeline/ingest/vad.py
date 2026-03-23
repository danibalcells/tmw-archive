"""FFmpeg silence detection (VAD) for raw session recordings.

Runs FFmpeg's silencedetect filter and parses its stderr output to produce
a list of (start_sec, end_sec) non-silent segments.

Tuning parameters are module-level constants. Expect to iterate on these
against real recordings — the defaults are a reasonable starting point for
room-mic band rehearsals.
"""

import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

SILENCE_THRESHOLD_DB: float = -40.0
MIN_SILENCE_DURATION: float = 2.0
MIN_SEGMENT_DURATION: float = 5.0

_SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)")
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")


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
    silence_threshold_db: float = SILENCE_THRESHOLD_DB,
    min_silence_duration: float = MIN_SILENCE_DURATION,
    min_segment_duration: float = MIN_SEGMENT_DURATION,
) -> list[tuple[float, float]]:
    """Run silencedetect on source and return non-silent (start, end) pairs in seconds.

    Segments shorter than min_segment_duration are discarded.
    """
    cmd = [
        "ffmpeg",
        "-i", str(source),
        "-af", f"silencedetect=noise={silence_threshold_db}dB:d={min_silence_duration}",
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
    i = 0
    while i < len(boundaries) - 1:
        seg_start = boundaries[i]
        seg_end = boundaries[i + 1]
        duration = seg_end - seg_start

        is_silent = any(
            abs(se - seg_start) < 0.01
            for se in silence_ends
        )

        if not is_silent and duration >= min_segment_duration:
            segments.append((round(seg_start, 3), round(seg_end, 3)))

        i += 1

    log.info(
        "VAD: %s → %d segments (threshold=%.0fdB, min_silence=%.1fs, min_segment=%.1fs)",
        source.name,
        len(segments),
        silence_threshold_db,
        min_silence_duration,
        min_segment_duration,
    )
    return segments
