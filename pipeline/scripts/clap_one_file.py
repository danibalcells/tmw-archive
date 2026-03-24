"""One-off diagnostic script: CLAP embedding on a single recording.

Isolates each step to identify failures:
  1. torch.load patching
  2. CLAP model loading
  3. Audio loading + resampling
  4. Segment slicing
  5. Batched inference
  6. Embedding packing

Usage:
  python -m pipeline.scripts.clap_one_file [--recording-id N]
"""

import sys
import time
import struct
import traceback

import numpy as np

CLAP_SR = 48_000
EMBEDDING_DIM = 512


def step(name: str):
    print(f"\n{'='*60}")
    print(f"STEP: {name}")
    print(f"{'='*60}")


def patch_torch():
    step("Patch torch.load for weights_only compatibility")
    import torch
    import torch.nn as nn

    print(f"  torch version: {torch.__version__}")

    _orig_load = torch.load
    _orig_load_state_dict = nn.Module.load_state_dict

    def _load_unsafe(*args, **kwargs):
        kwargs.setdefault("weights_only", False)
        return _orig_load(*args, **kwargs)

    def _load_state_dict_nonstrict(self, state_dict, strict=True, *args, **kwargs):
        return _orig_load_state_dict(self, state_dict, strict=False, *args, **kwargs)

    torch.load = _load_unsafe
    nn.Module.load_state_dict = _load_state_dict_nonstrict
    print(f"  Patched torch.load (weights_only=False)")
    print(f"  Patched nn.Module.load_state_dict (strict=False)")
    return _orig_load, _orig_load_state_dict


def load_clap_model():
    step("Load CLAP model")
    extra_args = sys.argv[1:]
    del sys.argv[1:]
    try:
        import laion_clap
        print(f"  laion_clap imported from: {laion_clap.__file__}")

        t0 = time.time()
        model = laion_clap.CLAP_Module(enable_fusion=True)
        print(f"  CLAP_Module created ({time.time()-t0:.1f}s)")

        t0 = time.time()
        model.load_ckpt()
        print(f"  Checkpoint loaded ({time.time()-t0:.1f}s)")

        model.eval()
        print("  Model set to eval mode")
        return model
    finally:
        sys.argv.extend(extra_args)


def load_audio(audio_path: str) -> np.ndarray:
    step(f"Load audio: {audio_path}")
    import librosa

    t0 = time.time()
    y, sr = librosa.load(audio_path, sr=CLAP_SR, mono=True)
    y = y.astype(np.float32)
    duration = len(y) / CLAP_SR
    print(f"  Loaded in {time.time()-t0:.1f}s")
    print(f"  Shape: {y.shape}, dtype: {y.dtype}")
    print(f"  Duration: {duration:.1f}s, SR: {CLAP_SR}")
    print(f"  Range: [{y.min():.4f}, {y.max():.4f}]")
    return y


def slice_segments(audio: np.ndarray, duration: float):
    step("Slice segments (20s windows, 10s step)")
    seg_duration = 20.0
    seg_step = 10.0
    target_samples = int(seg_duration * CLAP_SR)
    segments = []
    start = 0.0
    while start < duration:
        end = min(start + seg_duration, duration)
        start_i = int(round(start * CLAP_SR))
        end_i = min(int(round(end * CLAP_SR)), len(audio))
        window = audio[start_i:end_i]
        if len(window) < target_samples:
            window = np.pad(window, (0, target_samples - len(window)))
        segments.append((start, end, window))
        start += seg_step

    print(f"  {len(segments)} segments")
    if segments:
        print(f"  First: [{segments[0][0]:.1f}s - {segments[0][1]:.1f}s], shape={segments[0][2].shape}")
        print(f"  Last:  [{segments[-1][0]:.1f}s - {segments[-1][1]:.1f}s], shape={segments[-1][2].shape}")
    return segments


def run_inference(model, segments: list, batch_size: int = 4):
    step(f"Run CLAP inference (batch_size={batch_size})")
    windows = [seg[2] for seg in segments]
    n_segments = len(windows)
    embeddings = []

    for batch_start in range(0, n_segments, batch_size):
        batch_windows = windows[batch_start:batch_start + batch_size]
        batch = np.stack(batch_windows)
        print(f"  Batch {batch_start//batch_size + 1}: shape={batch.shape}", end="", flush=True)

        t0 = time.time()
        emb = model.get_audio_embedding_from_data(x=batch)
        elapsed = time.time() - t0
        emb = emb.astype(np.float32)
        print(f" -> embeddings {emb.shape} ({elapsed:.1f}s)")
        embeddings.append(emb)

        if batch_start == 0:
            print(f"  First embedding stats: mean={emb[0].mean():.4f}, std={emb[0].std():.4f}, norm={np.linalg.norm(emb[0]):.4f}")

    all_emb = np.concatenate(embeddings, axis=0)
    print(f"\n  Total: {all_emb.shape[0]} embeddings, each dim={all_emb.shape[1]}")

    packed = [struct.pack(f"<{EMBEDDING_DIM}f", *vec.tolist()) for vec in all_emb]
    print(f"  Packed size per embedding: {len(packed[0])} bytes")
    return all_emb, packed


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--recording-id", type=int, default=None)
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--max-segments", type=int, default=8, help="Cap segments for quick test")
    args = p.parse_args()

    step("Resolve recording")
    from dotenv import load_dotenv
    load_dotenv()

    from pipeline.config import PROCESSED_ROOT
    from pipeline.db.models import Recording
    from pipeline.db.session import SessionLocal

    db = SessionLocal()
    if args.recording_id:
        rec = db.get(Recording, args.recording_id)
    else:
        rec = db.query(Recording).filter(
            Recording.audio_path.isnot(None),
            Recording.duration_seconds.isnot(None),
            Recording.duration_seconds < 120,
        ).first()

    if not rec:
        print("No suitable recording found")
        sys.exit(1)

    audio_abs = str(PROCESSED_ROOT / rec.audio_path)
    print(f"  Recording id={rec.id}")
    print(f"  Path: {rec.audio_path}")
    print(f"  Duration: {rec.duration_seconds:.1f}s")
    print(f"  Absolute: {audio_abs}")
    db.close()

    orig_load, orig_lsd = patch_torch()

    try:
        model = load_clap_model()
    except Exception:
        traceback.print_exc()
        print("\nFATAL: Model loading failed. Cannot continue.")
        sys.exit(1)
    finally:
        import torch
        torch.load = orig_load
        torch.nn.Module.load_state_dict = orig_lsd

    try:
        import torchaudio
        print(f"\n  torchaudio version: {torchaudio.__version__}")
    except ImportError:
        print("\n  WARNING: torchaudio not installed — inference will fail")

    audio = load_audio(audio_abs)
    duration = len(audio) / CLAP_SR

    segments = slice_segments(audio, duration)
    if args.max_segments and len(segments) > args.max_segments:
        print(f"  Capping to {args.max_segments} segments for quick test")
        segments = segments[:args.max_segments]

    try:
        all_emb, packed = run_inference(model, segments, batch_size=args.batch_size)
    except Exception:
        traceback.print_exc()
        print("\nFATAL: Inference failed.")
        sys.exit(1)

    step("Summary")
    print(f"  Recording: {rec.id} ({rec.audio_path})")
    print(f"  Segments processed: {all_emb.shape[0]}")
    print(f"  Embedding dim: {all_emb.shape[1]}")
    print(f"  Packed bytes each: {len(packed[0])}")
    print(f"  All norms: {[f'{np.linalg.norm(v):.2f}' for v in all_emb]}")
    print("\nSUCCESS")


if __name__ == "__main__":
    main()
