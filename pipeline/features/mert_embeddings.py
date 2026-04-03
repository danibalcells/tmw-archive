"""MERT 1024-dim embedding extraction for recording segments.

Uses m-a-p/MERT-v1-330M, a music-specific self-supervised model trained on
160K hours. Outputs 1024-dim embeddings per segment (last hidden state,
mean-pooled across time).

Public API
----------
load_model(device)
    Load and return (model, processor) tuple.

compute_embeddings(task, model, processor, model_lock)
    Pure extraction — no SQLAlchemy, safe to call from a thread worker.

write_embeddings(result, segments, db)
    Write a result dict to the DB. Does not commit.

Task / Result shapes
--------------------
task = {
    "recording_id": int,
    "audio_abs": str,
    "segments": [(seg_id, start_sec, end_sec), ...],
}
result = {
    "recording_id": int,
    "embeddings": {seg_id: bytes},   # 1024 x float32 (4096 bytes) per segment
}
"""

import struct
import threading

import numpy as np
import librosa
import torch
from scipy.signal import butter, sosfilt
from sqlalchemy.orm import Session as DBSession

from pipeline.db.models import Segment

MERT_SR = 24_000
EMBEDDING_DIM = 1024

_HP_CUTOFF_HZ = 80.0
_HP_ORDER = 4
_NORM_PERCENTILE = 99.5
_NORM_TARGET_PEAK = 0.95


def load_model(device: str = "cpu") -> tuple:
    from transformers import AutoModel, Wav2Vec2FeatureExtractor

    processor = Wav2Vec2FeatureExtractor.from_pretrained(
        "m-a-p/MERT-v1-330M", trust_remote_code=True
    )
    model = AutoModel.from_pretrained(
        "m-a-p/MERT-v1-330M", trust_remote_code=True
    )
    model = model.to(device)
    model.eval()
    return model, processor


def _condition_audio(y: np.ndarray, sr: int) -> np.ndarray:
    peak = np.percentile(np.abs(y), _NORM_PERCENTILE)
    if peak > 1e-8:
        y = np.clip(y * (_NORM_TARGET_PEAK / peak), -1.0, 1.0)
    sos = butter(_HP_ORDER, _HP_CUTOFF_HZ / (sr / 2.0), btype="high", output="sos")
    return sosfilt(sos, y).astype(np.float32)


def _load_audio(audio_abs: str) -> np.ndarray:
    y, _ = librosa.load(audio_abs, sr=MERT_SR, mono=True)
    return _condition_audio(y, MERT_SR)


def _slice_window(audio: np.ndarray, start_sec: float, end_sec: float) -> np.ndarray:
    start_i = int(round(start_sec * MERT_SR))
    end_i = min(int(round(end_sec * MERT_SR)), len(audio))
    return audio[start_i:end_i]


def _pack_embedding(vec: np.ndarray) -> bytes:
    return struct.pack(f"<{EMBEDDING_DIM}f", *vec.tolist())


def compute_embeddings(
    task: dict,
    model,
    processor,
    model_lock: threading.Lock,
    device: str = "cpu",
) -> dict:
    audio_abs: str = task["audio_abs"]
    segments: list[tuple[int, float, float]] = task["segments"]

    audio = _load_audio(audio_abs)
    embeddings: dict[int, bytes] = {}

    for seg_id, start_sec, end_sec in segments:
        window = _slice_window(audio, start_sec, end_sec)
        if len(window) < 400:
            window = np.pad(window, (0, 400 - len(window)))

        inputs = processor(
            window,
            sampling_rate=MERT_SR,
            return_tensors="pt",
            padding=True,
        )
        input_values = inputs["input_values"].to(device)

        with model_lock:
            with torch.no_grad():
                outputs = model(input_values, output_hidden_states=True)

        last_hidden = outputs.hidden_states[-1].squeeze(0)
        pooled = last_hidden.mean(dim=0).cpu().numpy().astype(np.float32)
        embeddings[seg_id] = _pack_embedding(pooled)

    return {
        "recording_id": task["recording_id"],
        "embeddings": embeddings,
    }


def write_embeddings(
    result: dict,
    segments: list[Segment],
    db: DBSession,
) -> None:
    emb_by_seg_id = result["embeddings"]
    for seg in segments:
        packed = emb_by_seg_id.get(seg.id)
        if packed is not None:
            seg.mert_embedding = packed


def unpack_embedding(raw: bytes) -> np.ndarray:
    return np.frombuffer(raw, dtype="<f4").copy()
