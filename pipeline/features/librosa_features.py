"""Librosa-based feature extraction for a single recording.

Public API
----------
compute_features(task)  — pure extraction, no SQLAlchemy, safe to run in a
                          subprocess worker. Takes a task dict, returns a
                          result dict (both fully picklable).
write_features(result, recording, segments, db)
                        — writes a result dict to the DB. Must run in the
                          main process (single SQLite writer).

Task / Result shapes
--------------------
task = {
    "recording_id": int,
    "audio_abs": str,                          # absolute path to the MP3
    "segments": [(seg_id, start_sec, end_sec), ...],
}
result = {
    "recording_id": int,
    "timeseries": {                            # packed little-endian float32
        "rms": bytes,
        "spectral_centroid": bytes,
        "chroma": bytes,                       # n_seconds × 12 values, row-major
    },
    "segment_stats": [                         # one dict per segment
        {
            "id": int,
            "mean_rms": float,
            "var_rms": float,
            "mean_spectral_centroid": float,
            "var_spectral_centroid": float,
            "mean_chroma": bytes,              # 12 × float32
            "var_chroma": bytes,
        },
        ...
    ],
}

FeatureTimeseries storage
-------------------------
rms, spectral_centroid: n_seconds float32 values.
chroma: n_seconds × 12 float32 values packed row-major; reshape to
        (n_seconds, 12) on read.
"""

import numpy as np
import librosa
import soundfile as sf
from scipy.signal import butter, sosfilt, sosfilt_zi
from sqlalchemy.orm import Session as DBSession

from pipeline.db.models import FeatureTimeseries, Recording, Segment

SR = 22050
HOP_LENGTH = 512
CHUNK_SECONDS = 60

_HP_CUTOFF_HZ = 80.0
_HP_ORDER = 4
_NORM_PERCENTILE = 99.5
_NORM_TARGET_PEAK = 0.95


def _pack_f32(arr: np.ndarray) -> bytes:
    return arr.astype("<f4").tobytes()


def _bin_frames_to_seconds(
    values: np.ndarray, bins: np.ndarray, n_seconds: int
) -> np.ndarray:
    """Vectorized average of frame-level scalar values into 1-second bins."""
    counts = np.bincount(bins, minlength=n_seconds).astype(np.float32)
    sums = np.bincount(bins, weights=values.astype(np.float64), minlength=n_seconds)
    with np.errstate(invalid="ignore"):
        out = np.where(counts > 0, sums / counts, 0.0)
    return out.astype(np.float32)


def _bin_chroma_to_seconds(
    chroma: np.ndarray, bins: np.ndarray, n_seconds: int
) -> np.ndarray:
    """Vectorized average of 12-dim chroma frames into 1-second bins.

    chroma: (12, n_frames)
    Returns (n_seconds, 12) float32
    """
    counts = np.bincount(bins, minlength=n_seconds).astype(np.float64)
    out = np.zeros((n_seconds, 12), dtype=np.float32)
    for k in range(12):
        sums = np.bincount(bins, weights=chroma[k].astype(np.float64), minlength=n_seconds)
        with np.errstate(invalid="ignore"):
            out[:, k] = np.where(counts > 0, sums / counts, 0.0)
    return out


def _compute_norm_gain(audio_abs: str) -> float:
    """First pass over the file to compute a robust per-recording normalization gain.

    Reads the file in 10-second blocks at native sample rate, downsamples the
    absolute-value signal 10x for memory efficiency, then computes the global
    99.5th-percentile peak across all samples. Transients (mic grabs, setup
    bumps) occupy far less than 0.5% of a typical recording, so they don't
    influence the normalization target.
    """
    abs_samples: list[np.ndarray] = []
    with sf.SoundFile(audio_abs) as f:
        blocksize = f.samplerate * 10
        for block in f.blocks(blocksize=blocksize, dtype="float32", always_2d=True):
            y = block.mean(axis=1)
            abs_samples.append(np.abs(y[::10]))
    if not abs_samples:
        return 1.0
    peak = float(np.percentile(np.concatenate(abs_samples), _NORM_PERCENTILE))
    return (_NORM_TARGET_PEAK / peak) if peak > 1e-8 else 1.0


def compute_features(task: dict) -> dict:
    """Extract librosa features from an audio file. No DB access — subprocess-safe.

    Streams the file in CHUNK_SECONDS-sized blocks via soundfile so peak memory
    per worker is bounded (~50 MB) regardless of recording length. Designed to
    run in a ProcessPoolExecutor worker. Takes and returns plain picklable dicts.
    """
    audio_abs: str = task["audio_abs"]
    segments: list[tuple[int, float, float]] = task["segments"]

    norm_gain = _compute_norm_gain(audio_abs)

    rms_chunks: list[np.ndarray] = []
    sc_chunks: list[np.ndarray] = []
    chroma_chunks: list[np.ndarray] = []

    hp_sos: np.ndarray | None = None
    hp_zi: np.ndarray | None = None

    with sf.SoundFile(audio_abs) as f:
        native_sr = f.samplerate
        blocksize = CHUNK_SECONDS * native_sr

        for block in f.blocks(blocksize=blocksize, dtype="float32", always_2d=True):
            y_chunk = block.mean(axis=1)

            y_chunk = np.clip(y_chunk * norm_gain, -1.0, 1.0)

            if hp_sos is None:
                hp_sos = butter(_HP_ORDER, _HP_CUTOFF_HZ / (native_sr / 2.0), btype="high", output="sos")
                hp_zi = sosfilt_zi(hp_sos) * y_chunk[0]
            y_chunk, hp_zi = sosfilt(hp_sos, y_chunk, zi=hp_zi)

            if native_sr != SR:
                y_chunk = librosa.resample(y_chunk, orig_sr=native_sr, target_sr=SR)

            n_chunk_seconds = max(1, int(np.ceil(len(y_chunk) / SR)))
            rms_f = librosa.feature.rms(y=y_chunk, hop_length=HOP_LENGTH)[0]
            sc_f = librosa.feature.spectral_centroid(y=y_chunk, sr=SR, hop_length=HOP_LENGTH)[0]
            chroma_f = librosa.feature.chroma_stft(y=y_chunk, sr=SR, hop_length=HOP_LENGTH, tuning=0.0)

            frame_times = librosa.frames_to_time(np.arange(len(rms_f)), sr=SR, hop_length=HOP_LENGTH)
            bins = np.clip(np.floor(frame_times).astype(int), 0, n_chunk_seconds - 1)

            rms_chunks.append(_bin_frames_to_seconds(rms_f, bins, n_chunk_seconds))
            sc_chunks.append(_bin_frames_to_seconds(sc_f, bins, n_chunk_seconds))
            chroma_chunks.append(_bin_chroma_to_seconds(chroma_f, bins, n_chunk_seconds))

    rms_s = np.concatenate(rms_chunks)
    sc_s = np.concatenate(sc_chunks)
    chroma_s = np.concatenate(chroma_chunks, axis=0)
    n_seconds = len(rms_s)

    segment_stats = []
    for seg_id, start_sec, end_sec in segments:
        start_i = int(start_sec)
        end_i = min(int(np.ceil(end_sec)), n_seconds)
        seg_rms = rms_s[start_i:end_i]
        seg_sc = sc_s[start_i:end_i]
        seg_chroma = chroma_s[start_i:end_i]

        if len(seg_rms) == 0:
            continue

        segment_stats.append({
            "id": seg_id,
            "mean_rms": float(seg_rms.mean()),
            "var_rms": float(seg_rms.var()),
            "mean_spectral_centroid": float(seg_sc.mean()),
            "var_spectral_centroid": float(seg_sc.var()),
            "mean_chroma": _pack_f32(seg_chroma.mean(axis=0)),
            "var_chroma": _pack_f32(seg_chroma.var(axis=0)),
        })

    return {
        "recording_id": task["recording_id"],
        "timeseries": {
            "rms": _pack_f32(rms_s),
            "spectral_centroid": _pack_f32(sc_s),
            "chroma": _pack_f32(chroma_s),
        },
        "segment_stats": segment_stats,
    }


def write_features(
    result: dict,
    recording: Recording,
    segments: list[Segment],
    db: DBSession,
) -> None:
    """Persist a compute_features result to the DB. Does not commit."""
    seg_by_id = {s.id: s for s in segments}

    for feature_name, packed in result["timeseries"].items():
        existing = (
            db.query(FeatureTimeseries)
            .filter(
                FeatureTimeseries.recording_id == recording.id,
                FeatureTimeseries.feature_name == feature_name,
            )
            .first()
        )
        if existing:
            existing.packed_values = packed
        else:
            db.add(FeatureTimeseries(
                recording_id=recording.id,
                feature_name=feature_name,
                packed_values=packed,
            ))

    for stats in result["segment_stats"]:
        seg = seg_by_id.get(stats["id"])
        if seg is None:
            continue
        seg.mean_rms = stats["mean_rms"]
        seg.var_rms = stats["var_rms"]
        seg.mean_spectral_centroid = stats["mean_spectral_centroid"]
        seg.var_spectral_centroid = stats["var_spectral_centroid"]
        seg.mean_chroma = stats["mean_chroma"]
        seg.var_chroma = stats["var_chroma"]
