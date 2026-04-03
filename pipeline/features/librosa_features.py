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
            "mean_rms": float, "var_rms": float,
            "mean_spectral_centroid": float, "var_spectral_centroid": float,
            "mean_chroma": bytes, "var_chroma": bytes,
            "mean_mfcc": bytes, "var_mfcc": bytes,
            "mean_spectral_bandwidth": float, "var_spectral_bandwidth": float,
            "mean_spectral_flatness": float, "var_spectral_flatness": float,
            "mean_spectral_rolloff": float, "var_spectral_rolloff": float,
            "mean_zcr": float, "var_zcr": float,
            "onset_density": float,
            "mean_spectral_contrast": bytes, "var_spectral_contrast": bytes,
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


def _bin_multidim_to_seconds(
    mat: np.ndarray, bins: np.ndarray, n_seconds: int
) -> np.ndarray:
    """Vectorized average of (D, n_frames) feature matrix into (n_seconds, D).

    Works for chroma (12,), MFCCs (13,), spectral contrast (7,), etc.
    """
    d = mat.shape[0]
    counts = np.bincount(bins, minlength=n_seconds).astype(np.float64)
    out = np.zeros((n_seconds, d), dtype=np.float32)
    for k in range(d):
        sums = np.bincount(bins, weights=mat[k].astype(np.float64), minlength=n_seconds)
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
    mfcc_chunks: list[np.ndarray] = []
    sbw_chunks: list[np.ndarray] = []
    sf_chunks: list[np.ndarray] = []
    sr_chunks: list[np.ndarray] = []
    zcr_chunks: list[np.ndarray] = []
    onset_chunks: list[np.ndarray] = []
    scontrast_chunks: list[np.ndarray] = []

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

            S = np.abs(librosa.stft(y_chunk, hop_length=HOP_LENGTH))

            rms_f = librosa.feature.rms(S=S, hop_length=HOP_LENGTH)[0]
            sc_f = librosa.feature.spectral_centroid(S=S, sr=SR, hop_length=HOP_LENGTH)[0]
            chroma_f = librosa.feature.chroma_stft(S=S, sr=SR, hop_length=HOP_LENGTH, tuning=0.0)
            mfcc_f = librosa.feature.mfcc(S=librosa.power_to_db(S**2), sr=SR, n_mfcc=13)
            sbw_f = librosa.feature.spectral_bandwidth(S=S, sr=SR, hop_length=HOP_LENGTH)[0]
            sf_f = librosa.feature.spectral_flatness(S=S, hop_length=HOP_LENGTH)[0]
            sr_f = librosa.feature.spectral_rolloff(S=S, sr=SR, hop_length=HOP_LENGTH)[0]
            zcr_f = librosa.feature.zero_crossing_rate(y_chunk, hop_length=HOP_LENGTH)[0]
            onset_f = librosa.onset.onset_strength(S=librosa.power_to_db(S**2), sr=SR, hop_length=HOP_LENGTH)
            scontrast_f = librosa.feature.spectral_contrast(S=S, sr=SR, hop_length=HOP_LENGTH)

            frame_times = librosa.frames_to_time(np.arange(len(rms_f)), sr=SR, hop_length=HOP_LENGTH)
            bins = np.clip(np.floor(frame_times).astype(int), 0, n_chunk_seconds - 1)

            rms_chunks.append(_bin_frames_to_seconds(rms_f, bins, n_chunk_seconds))
            sc_chunks.append(_bin_frames_to_seconds(sc_f, bins, n_chunk_seconds))
            chroma_chunks.append(_bin_multidim_to_seconds(chroma_f, bins, n_chunk_seconds))
            mfcc_chunks.append(_bin_multidim_to_seconds(mfcc_f, bins, n_chunk_seconds))
            sbw_chunks.append(_bin_frames_to_seconds(sbw_f, bins, n_chunk_seconds))
            sf_chunks.append(_bin_frames_to_seconds(sf_f, bins, n_chunk_seconds))
            sr_chunks.append(_bin_frames_to_seconds(sr_f, bins, n_chunk_seconds))
            zcr_chunks.append(_bin_frames_to_seconds(zcr_f, bins, n_chunk_seconds))

            onset_times = librosa.frames_to_time(np.arange(len(onset_f)), sr=SR, hop_length=HOP_LENGTH)
            onset_bins = np.clip(np.floor(onset_times).astype(int), 0, n_chunk_seconds - 1)
            onset_chunks.append(_bin_frames_to_seconds(onset_f, onset_bins, n_chunk_seconds))

            scontrast_chunks.append(_bin_multidim_to_seconds(scontrast_f, bins, n_chunk_seconds))

    rms_s = np.concatenate(rms_chunks)
    sc_s = np.concatenate(sc_chunks)
    chroma_s = np.concatenate(chroma_chunks, axis=0)
    mfcc_s = np.concatenate(mfcc_chunks, axis=0)
    sbw_s = np.concatenate(sbw_chunks)
    sf_s = np.concatenate(sf_chunks)
    sr_s = np.concatenate(sr_chunks)
    zcr_s = np.concatenate(zcr_chunks)
    onset_s = np.concatenate(onset_chunks)
    scontrast_s = np.concatenate(scontrast_chunks, axis=0)
    n_seconds = len(rms_s)

    segment_stats = []
    for seg_id, start_sec, end_sec in segments:
        start_i = int(start_sec)
        end_i = min(int(np.ceil(end_sec)), n_seconds)
        seg_rms = rms_s[start_i:end_i]
        if len(seg_rms) == 0:
            continue

        seg_sc = sc_s[start_i:end_i]
        seg_chroma = chroma_s[start_i:end_i]
        seg_mfcc = mfcc_s[start_i:end_i]
        seg_sbw = sbw_s[start_i:end_i]
        seg_sf = sf_s[start_i:end_i]
        seg_sr = sr_s[start_i:end_i]
        seg_zcr = zcr_s[start_i:end_i]
        seg_onset = onset_s[start_i:end_i]
        seg_scontrast = scontrast_s[start_i:end_i]

        threshold = float(seg_onset.mean() + 0.5 * seg_onset.std()) if seg_onset.std() > 0 else float(seg_onset.mean())
        n_onsets = int(np.sum(seg_onset > threshold))
        seg_dur = end_sec - start_sec
        onset_density_val = n_onsets / seg_dur if seg_dur > 0 else 0.0

        segment_stats.append({
            "id": seg_id,
            "mean_rms": float(seg_rms.mean()),
            "var_rms": float(seg_rms.var()),
            "mean_spectral_centroid": float(seg_sc.mean()),
            "var_spectral_centroid": float(seg_sc.var()),
            "mean_chroma": _pack_f32(seg_chroma.mean(axis=0)),
            "var_chroma": _pack_f32(seg_chroma.var(axis=0)),
            "mean_mfcc": _pack_f32(seg_mfcc.mean(axis=0)),
            "var_mfcc": _pack_f32(seg_mfcc.var(axis=0)),
            "mean_spectral_bandwidth": float(seg_sbw.mean()),
            "var_spectral_bandwidth": float(seg_sbw.var()),
            "mean_spectral_flatness": float(seg_sf.mean()),
            "var_spectral_flatness": float(seg_sf.var()),
            "mean_spectral_rolloff": float(seg_sr.mean()),
            "var_spectral_rolloff": float(seg_sr.var()),
            "mean_zcr": float(seg_zcr.mean()),
            "var_zcr": float(seg_zcr.var()),
            "onset_density": onset_density_val,
            "mean_spectral_contrast": _pack_f32(seg_scontrast.mean(axis=0)),
            "var_spectral_contrast": _pack_f32(seg_scontrast.var(axis=0)),
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
        seg.mean_mfcc = stats.get("mean_mfcc")
        seg.var_mfcc = stats.get("var_mfcc")
        seg.mean_spectral_bandwidth = stats.get("mean_spectral_bandwidth")
        seg.var_spectral_bandwidth = stats.get("var_spectral_bandwidth")
        seg.mean_spectral_flatness = stats.get("mean_spectral_flatness")
        seg.var_spectral_flatness = stats.get("var_spectral_flatness")
        seg.mean_spectral_rolloff = stats.get("mean_spectral_rolloff")
        seg.var_spectral_rolloff = stats.get("var_spectral_rolloff")
        seg.mean_zcr = stats.get("mean_zcr")
        seg.var_zcr = stats.get("var_zcr")
        seg.onset_density = stats.get("onset_density")
        seg.mean_spectral_contrast = stats.get("mean_spectral_contrast")
        seg.var_spectral_contrast = stats.get("var_spectral_contrast")
