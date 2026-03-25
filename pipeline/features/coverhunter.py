"""CoverHunter 128-dim embedding extraction for recordings.

Public API
----------
load_model()
    Load and return a CoverHunterMPS model bundle. Call once per process.
    Requires COVERHUNTER_MODEL_DIR env var; optionally COVERHUNTER_SRC_DIR
    to locate the CoverHunterMPS source on sys.path.

compute_embedding(audio_path, model)
    Compute one 128-dim float32 embedding for a full audio file.

pack_embedding(vec) / unpack_embedding(raw)
    Serialize/deserialize a (128,) float32 array to/from 512 bytes.

Threading
---------
Audio loading and CQT computation (_load_feat) are safe to run concurrently
across threads. Model inference (_run_inference) must be serialized — callers
that use a thread pool should acquire a shared Lock around _run_inference.
compute_embedding combines both steps and is not internally locked.
"""

import os
import struct
import sys
from typing import Any

import librosa
import numpy as np
import torch
import yaml

EMBEDDING_DIM = 128

_SR = 16_000
_N_BINS = 96
_BINS_PER_OCTAVE = 12
_FMIN = 32.0
_HOP_LENGTH = 640  # 0.04 s × 16 kHz


def _load_hparams(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _shorter(feat: np.ndarray, mean_size: int) -> np.ndarray:
    """Temporal downsampling: average consecutive mean_size CQT frames.

    Inlined from CoverHunterMPS src/cqt.py::shorter(). The pretrained model
    was trained with this exact downsampling, so results must match precisely.
    """
    if mean_size == 1:
        return feat
    cqt = feat.T
    height, length = cqt.shape
    new_len = int(length / mean_size)
    new_cqt = np.zeros((height, new_len), dtype=np.float32)
    for i in range(new_len):
        new_cqt[:, i] = cqt[:, i * mean_size : (i + 1) * mean_size].mean(axis=1)
    return new_cqt.T


def _compute_cqt(signal: np.ndarray, device_str: str) -> np.ndarray:
    """Compute CQT spectrogram matching CoverHunterMPS identify.py::_make_feat().

    Uses nnAudio's CQT (MPS/CPU) or CQT2010v2 (CUDA) to match the exact
    transform used during training. Normalises to 0.999 peak and converts to
    dB scale relative to the per-file maximum.
    """
    from nnAudio.features.cqt import CQT, CQT2010v2

    transform_cls = CQT2010v2 if device_str == "cuda" else CQT
    device = torch.device(device_str)

    t = torch.from_numpy(signal).float().unsqueeze(0).to(device)
    t = t / torch.max(torch.tensor(0.001, device=device), torch.max(torch.abs(t))) * 0.999

    cqt = transform_cls(
        _SR,
        hop_length=_HOP_LENGTH,
        n_bins=_N_BINS,
        fmin=_FMIN,
        bins_per_octave=_BINS_PER_OCTAVE,
        verbose=False,
    ).to(device)(t)

    cqt = cqt + 1e-9
    cqt = cqt.squeeze(0)
    ref_log = torch.log10(torch.max(cqt))
    cqt = 20 * torch.log10(cqt) - 20 * ref_log
    cqt = torch.swapaxes(cqt, 0, 1)
    return cqt.numpy(force=True)


def _load_feat(audio_path: str, bundle: Any) -> torch.Tensor:
    """Load audio, compute CQT, and return a model-ready tensor.

    Safe to call concurrently from multiple threads — no model state is touched.
    """
    infer_frames: int = bundle["infer_frames"]
    mean_size: int = bundle["mean_size"]
    device_str: str = bundle["device_str"]

    # Trim audio before CQT to avoid loading full long recordings into GPU memory.
    # Add 1 extra hop of headroom for the CQT boundary.
    max_samples = (infer_frames + 1) * _HOP_LENGTH
    signal, _ = librosa.load(audio_path, sr=_SR, mono=True, duration=max_samples / _SR)
    cqt = _compute_cqt(signal, device_str)

    cqt = cqt[:infer_frames]
    if len(cqt) < infer_frames:
        cqt = np.pad(cqt, ((0, infer_frames - len(cqt)), (0, 0)), constant_values=-100)

    cqt = _shorter(cqt, mean_size)
    return torch.from_numpy(cqt)


def _run_inference(feat: torch.Tensor, bundle: Any) -> np.ndarray:
    """Run a prepared feature tensor through the model.

    Must be serialized across threads — acquire a shared Lock before calling.
    Returns a (128,) float32 array.
    """
    device = torch.device(bundle["device_str"])
    feat_t = feat.unsqueeze(0).to(device)
    embed, _ = bundle["model"].inference(feat_t)
    return embed.cpu().numpy()[0].astype(np.float32)


def load_model() -> Any:
    """Load and return the CoverHunterMPS model bundle.

    Environment variables:
      COVERHUNTER_MODEL_DIR  — path to model folder (required). Must contain:
                               config/hparams.yaml and checkpoints/g_000000NN.
      COVERHUNTER_SRC_DIR    — path to CoverHunterMPS repo root (optional).
                               Added to sys.path so `from src.model import Model`
                               resolves. Falls back to the vendor stub if unset
                               or if the import still fails.

    Returns a bundle dict with keys: model, device_str, infer_frames, mean_size.
    """
    model_dir = os.environ.get("COVERHUNTER_MODEL_DIR")
    if not model_dir:
        raise RuntimeError(
            "COVERHUNTER_MODEL_DIR is not set. "
            "See pipeline/vendor/coverhunter_mps/MODEL_SETUP.md."
        )

    src_dir = os.environ.get("COVERHUNTER_SRC_DIR")
    if src_dir and src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    try:
        from src.model import Model  # type: ignore[import-not-found]
    except ImportError:
        from pipeline.vendor.coverhunter_mps import Model  # stub — raises NotImplementedError

    hp_path = os.path.join(model_dir, "config", "hparams.yaml")
    hp = _load_hparams(hp_path)

    forced = os.environ.get("COVERHUNTER_DEVICE", "").lower()
    if forced in ("cpu", "mps", "cuda"):
        device_str = forced
    elif torch.backends.mps.is_available():
        device_str = "mps"
    elif torch.cuda.is_available():
        device_str = "cuda"
    else:
        device_str = "cpu"

    hp["device"] = device_str
    device = torch.device(device_str)

    model = Model(hp).to(device)
    checkpoint_dir = os.path.join(model_dir, "checkpoints")
    model.load_model_parameters(checkpoint_dir, device=device_str)
    model.eval()

    chunk_frames = hp["chunk_frame"]
    if isinstance(chunk_frames, list):
        chunk_frames = chunk_frames[0]
    mean_size: int = hp["mean_size"]
    infer_frames: int = chunk_frames * mean_size

    return {
        "model": model,
        "device_str": device_str,
        "infer_frames": infer_frames,
        "mean_size": mean_size,
    }


def compute_embedding(audio_path: str, model: Any) -> np.ndarray:
    """Compute a 128-dim float32 CoverHunter embedding for the full audio file.

    Audio is loaded at 16 kHz mono. CQT spectrogram computed with n_bins=96,
    bins_per_octave=12, fmin=32 Hz, hop_size=0.04 s (640 samples).
    Returns a (128,) float32 numpy array.
    """
    feat = _load_feat(audio_path, model)
    return _run_inference(feat, model)


def pack_embedding(vec: np.ndarray) -> bytes:
    """Pack (128,) float32 array to 512 bytes (little-endian)."""
    return struct.pack(f"<{EMBEDDING_DIM}f", *vec.tolist())


def unpack_embedding(raw: bytes) -> np.ndarray:
    """Unpack 512 bytes to (128,) float32 array."""
    return np.frombuffer(raw, dtype="<f4").copy()
