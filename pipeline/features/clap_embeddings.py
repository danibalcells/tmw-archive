"""LAION-CLAP 512-dim embedding extraction for recording segments.

Public API
----------
load_model(device)
    Load and return a CLAP_Module, ready for inference. Call once per process
    (or once per thread if using multiple model instances).

compute_embeddings(task, model, model_lock, batch_size)
    Pure extraction — no SQLAlchemy, safe to call from a thread worker.
    Streams audio via librosa, slices segment windows, runs batched inference.
    Returns a result dict.

write_embeddings(result, segments, db)
    Write a result dict to the DB. Does not commit.

Task / Result shapes
--------------------
task = {
    "recording_id": int,
    "audio_abs": str,                     # absolute path to the MP3
    "segments": [(seg_id, start_sec, end_sec), ...],
}
result = {
    "recording_id": int,
    "embeddings": {seg_id: bytes},        # 512 × float32 (2048 bytes) per segment
}

Threading model
---------------
One shared CLAP_Module is loaded in the main thread. Worker threads each load
audio independently (I/O overlap) then acquire model_lock to run the batched
forward pass. PyTorch's internal threading (MKL/OpenBLAS) saturates the CPU
during each inference call. The lock ensures model state is not accessed
concurrently while still allowing audio loading to proceed in parallel.
"""

import threading
import struct

import numpy as np
import librosa
from sqlalchemy.orm import Session as DBSession

from pipeline.db.models import Segment

CLAP_SR = 48_000
EMBEDDING_DIM = 512
SEGMENT_SAMPLES = int(20 * CLAP_SR)


def load_model(device: str = "cpu"):
    import sys

    # laion-clap's training/data.py calls argparse.parse_args() at module
    # import time (line 40: `args = parse_args()`), which reads sys.argv and
    # fails if it contains unrecognized args. Clear sys.argv in-place BEFORE
    # the import so the module-level call sees an empty argv. Restore after.
    extra_args = sys.argv[1:]
    del sys.argv[1:]
    try:
        import torch

        # PyTorch 2.6+ sets weights_only=True by default, which rejects the numpy
        # scalar types present in the laion-clap checkpoint. Monkey-patch torch.load
        # to force weights_only=False for the duration of model loading. The
        # checkpoint is from the official LAION HuggingFace repo and is trusted.
        import torch.nn as nn

        _orig_load = torch.load
        _orig_load_state_dict = nn.Module.load_state_dict

        def _load_unsafe(*args, **kwargs):
            kwargs.setdefault("weights_only", False)
            return _orig_load(*args, **kwargs)

        def _load_state_dict_nonstrict(self, state_dict, strict=True, *args, **kwargs):
            # Position_ids is a buffer added by newer transformers versions that
            # may not be present in the model; ignore it rather than crashing.
            return _orig_load_state_dict(self, state_dict, strict=False, *args, **kwargs)

        torch.load = _load_unsafe
        nn.Module.load_state_dict = _load_state_dict_nonstrict
        try:
            import laion_clap
            model = laion_clap.CLAP_Module(enable_fusion=True)
            model.load_ckpt()
        finally:
            torch.load = _orig_load
            nn.Module.load_state_dict = _orig_load_state_dict
    finally:
        sys.argv.extend(extra_args)

    model.eval()
    return model


def _load_audio(audio_abs: str) -> np.ndarray:
    """Load audio file as mono float32 at CLAP_SR.

    Uses librosa which handles MP3 reliably and resamples in one pass.
    Returns a 1-D float32 array of length n_samples.
    """
    y, _ = librosa.load(audio_abs, sr=CLAP_SR, mono=True)
    return y.astype(np.float32)


def _slice_window(audio: np.ndarray, start_sec: float, end_sec: float) -> np.ndarray:
    """Extract a segment window, always padded to SEGMENT_SAMPLES for uniform batching."""
    start_i = int(round(start_sec * CLAP_SR))
    end_i = min(int(round(end_sec * CLAP_SR)), len(audio))
    window = audio[start_i:end_i]
    if len(window) < SEGMENT_SAMPLES:
        window = np.pad(window, (0, SEGMENT_SAMPLES - len(window)))
    return window


def _run_batch(model, windows: list[np.ndarray]) -> np.ndarray:
    """Run a batch of audio windows through CLAP. Returns (N, 512) float32 array."""
    batch = np.stack(windows)
    embeddings = model.get_audio_embedding_from_data(x=batch)
    return embeddings.astype(np.float32)


def _pack_embedding(vec: np.ndarray) -> bytes:
    return struct.pack(f"<{EMBEDDING_DIM}f", *vec.tolist())


def compute_embeddings(
    task: dict,
    model,
    model_lock: threading.Lock,
    batch_size: int = 16,
) -> dict:
    """Extract CLAP embeddings for all segments in a recording.

    Thread-safe: audio loading is concurrent; model inference is serialized via
    model_lock so multiple threads don't fight over PyTorch's internal state.
    """
    audio_abs: str = task["audio_abs"]
    segments: list[tuple[int, float, float]] = task["segments"]

    audio = _load_audio(audio_abs)

    embeddings: dict[int, bytes] = {}
    seg_ids = [seg_id for seg_id, _, _ in segments]
    windows = [_slice_window(audio, start, end) for _, start, end in segments]

    for batch_start in range(0, len(windows), batch_size):
        batch_windows = windows[batch_start : batch_start + batch_size]
        batch_ids = seg_ids[batch_start : batch_start + batch_size]

        with model_lock:
            batch_emb = _run_batch(model, batch_windows)

        for seg_id, vec in zip(batch_ids, batch_emb):
            embeddings[seg_id] = _pack_embedding(vec)

    return {
        "recording_id": task["recording_id"],
        "embeddings": embeddings,
    }


def write_embeddings(
    result: dict,
    segments: list[Segment],
    db: DBSession,
) -> None:
    """Persist embeddings from a result dict to the DB. Does not commit."""
    emb_by_seg_id = result["embeddings"]
    for seg in segments:
        packed = emb_by_seg_id.get(seg.id)
        if packed is not None:
            seg.clap_embedding = packed


def unpack_embedding(raw: bytes) -> np.ndarray:
    """Deserialize a stored embedding back to a (512,) float32 array."""
    return np.frombuffer(raw, dtype="<f4").copy()
