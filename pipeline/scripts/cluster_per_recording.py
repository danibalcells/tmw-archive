"""Per-recording passage segmentation via Self-Similarity Matrix + novelty detection.

Instead of clustering segments globally (which produces musically incoherent clusters),
this script segments each recording individually using its own CLAP embedding structure,
then lifts the resulting passages into a global vocabulary for downstream analysis.

Method: Foote (2000) — Self-Similarity Matrix + checkerboard kernel novelty curve.

Segments are 20s windows with 50% overlap (10s step).

Usage:
  python -m pipeline.scripts.cluster_per_recording
  python -m pipeline.scripts.cluster_per_recording --kernel-size 16 --peak-threshold 1.5
  python -m pipeline.scripts.cluster_per_recording --explore
  python -m pipeline.scripts.cluster_per_recording --name my-run

Options:
  --kernel-size N           Checkerboard kernel size in segments (default: 8)
  --peak-threshold F        Novelty peak threshold as mean + F*std (default: 1.0)
  --min-segments N          Min segments to attempt segmentation; fewer → single passage (default: 6)
  --min-passage-duration F  Minimum passage duration in seconds (default: 30.0)
  --name NAME               Run name / output subdirectory (default: auto from params)
  --output-dir DIR          Root output directory (default: data/eternal-rehearsal)
  --explore                 Generate HTML explorer, start servers, and open in browser
  --explore-n N             Max recordings to include in explorer (default: 20)
  --port PORT               Preferred audio server port (default: 8765)
"""

import argparse
import base64
import json
import logging
import os
import struct
import zlib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from scipy.signal import find_peaks

load_dotenv()

from pipeline.db.models import Recording, Segment
from pipeline.db.models import Session as DbSession
from pipeline.db.models import Song
from pipeline.db.session import SessionLocal

DEFAULT_OUTPUT_DIR = Path("data/eternal-rehearsal")
SEGMENT_STEP_SECONDS = 10.0  # 20s window, 50% overlap → 10s step

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB loading
# ---------------------------------------------------------------------------

def _unpack(raw: bytes) -> np.ndarray:
    return np.frombuffer(raw, dtype="<f4").copy()


def _effective_type(content_type: str | None, song_type: str | None) -> str:
    non_musical = {"banter", "tuning", "noodling", "count_in", "silence", "other"}
    if content_type == "song_take":
        return song_type or "unreviewed"
    if content_type == "jam":
        return "jam"
    if content_type in non_musical:
        return "non-musical"
    return "unreviewed"


def _load_segments(db) -> list[dict]:
    rows = (
        db.query(
            Segment.id,
            Segment.recording_id,
            Segment.start_seconds,
            Segment.end_seconds,
            Segment.clap_embedding,
            Segment.mean_rms,
            Segment.mean_spectral_centroid,
            Recording.title.label("recording_title"),
            Recording.audio_path,
            Recording.content_type,
            DbSession.date.label("session_date"),
            Song.title.label("song_title"),
            Song.song_type,
        )
        .join(Recording, Segment.recording_id == Recording.id)
        .outerjoin(DbSession, Recording.session_id == DbSession.id)
        .outerjoin(Song, Recording.song_id == Song.id)
        .filter(Segment.clap_embedding.isnot(None))
        .all()
    )
    return [
        {
            "segment_id": int(r.id),
            "recording_id": int(r.recording_id),
            "start_seconds": float(r.start_seconds),
            "end_seconds": float(r.end_seconds),
            "embedding": _unpack(r.clap_embedding),
            "mean_rms": float(r.mean_rms) if r.mean_rms is not None else None,
            "mean_spectral_centroid": (
                float(r.mean_spectral_centroid)
                if r.mean_spectral_centroid is not None
                else None
            ),
            "recording_title": r.recording_title,
            "audio_path": r.audio_path,
            "session_date": r.session_date,
            "song_title": r.song_title,
            "effective_type": _effective_type(r.content_type, r.song_type),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# SSM + novelty curve
# ---------------------------------------------------------------------------

def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return matrix / norms


def _checkerboard_kernel(size: int) -> np.ndarray:
    """Foote (2000) checkerboard kernel: +1 on diagonal blocks, -1 on off-diagonal."""
    k = np.ones((size, size), dtype=np.float32)
    half = size // 2
    k[:half, half:] = -1.0
    k[half:, :half] = -1.0
    return k


def _novelty_curve(ssm: np.ndarray, kernel_size: int) -> np.ndarray:
    """Slide checkerboard kernel along SSM diagonal → novelty curve (Foote 2000)."""
    n = ssm.shape[0]
    half = kernel_size // 2
    kernel = _checkerboard_kernel(kernel_size)
    novelty = np.zeros(n, dtype=np.float32)

    for i in range(n):
        r0, r1 = max(0, i - half), min(n, i + half)
        c0, c1 = max(0, i - half), min(n, i + half)
        block = ssm[r0:r1, c0:c1]
        kr0 = r0 - (i - half)
        kc0 = c0 - (i - half)
        kr1 = kr0 + (r1 - r0)
        kc1 = kc0 + (c1 - c0)
        novelty[i] = float(np.sum(block * kernel[kr0:kr1, kc0:kc1]))

    return novelty


def _pick_boundaries(
    novelty: np.ndarray,
    peak_threshold: float,
    min_distance: int,
) -> list[int]:
    """Return segment indices that are passage boundaries (peaks in novelty curve)."""
    if len(novelty) < 3:
        return []
    height = float(novelty.mean() + peak_threshold * novelty.std())
    peaks, _ = find_peaks(novelty, height=height, distance=max(1, min_distance))
    return peaks.tolist()


# ---------------------------------------------------------------------------
# Passage extraction
# ---------------------------------------------------------------------------

def _extract_passages(
    segments: list[dict],
    embeddings_normed: np.ndarray,
    boundaries: list[int],
    passage_id_start: int,
) -> tuple[list[dict], list[np.ndarray]]:
    """Convert boundary indices into passage dicts and their embeddings.

    Returns (passages, passage_embeddings).
    """
    n = len(segments)
    splits = sorted({0} | {b for b in boundaries if 0 < b < n} | {n})

    passages: list[dict] = []
    embeddings: list[np.ndarray] = []

    for local_idx, (start_idx, end_idx) in enumerate(zip(splits, splits[1:])):
        group = segments[start_idx:end_idx]
        rms_vals = [s["mean_rms"] for s in group if s["mean_rms"] is not None]
        cent_vals = [
            s["mean_spectral_centroid"]
            for s in group
            if s["mean_spectral_centroid"] is not None
        ]

        mean_emb = embeddings_normed[start_idx:end_idx].mean(axis=0)
        norm = np.linalg.norm(mean_emb)
        passage_emb = mean_emb / norm if norm > 0 else mean_emb
        embeddings.append(passage_emb)

        passages.append({
            "passage_id": passage_id_start + local_idx,
            "passage_type": local_idx,
            "recording_id": group[0]["recording_id"],
            "segment_ids": [s["segment_id"] for s in group],
            "start_seconds": group[0]["start_seconds"],
            "end_seconds": group[-1]["end_seconds"],
            "duration": round(group[-1]["end_seconds"] - group[0]["start_seconds"], 2),
            "segment_count": len(group),
            "mean_rms": round(float(np.mean(rms_vals)), 6) if rms_vals else None,
            "mean_spectral_centroid": (
                round(float(np.mean(cent_vals)), 2) if cent_vals else None
            ),
            "recording_title": group[0]["recording_title"],
            "audio_path": group[0]["audio_path"],
            "session_date": group[0]["session_date"],
            "song_title": group[0]["song_title"],
            "effective_type": group[0]["effective_type"],
        })

    return passages, embeddings


# ---------------------------------------------------------------------------
# Passage types summary (API / React UI compatibility)
# ---------------------------------------------------------------------------

def _build_passage_types(passages: list[dict]) -> dict:
    by_type: dict[int, list[dict]] = defaultdict(list)
    for p in passages:
        by_type[p["passage_type"]].append(p)

    types: dict[str, dict] = {}
    for type_id in sorted(by_type):
        group = by_type[type_id]
        rms_vals = [p["mean_rms"] for p in group if p["mean_rms"] is not None]
        cent_vals = [
            p["mean_spectral_centroid"]
            for p in group
            if p["mean_spectral_centroid"] is not None
        ]
        durations = [p["duration"] for p in group]
        recordings = {p["recording_id"] for p in group}
        song_counts: dict[str, int] = {}
        for p in group:
            if p.get("song_title"):
                song_counts[p["song_title"]] = song_counts.get(p["song_title"], 0) + 1
        top_songs = sorted(song_counts.items(), key=lambda x: -x[1])[:5]

        types[str(type_id)] = {
            "type_id": type_id,
            "count": len(group),
            "n_recordings": len(recordings),
            "mean_duration": round(float(np.mean(durations)), 1),
            "mean_rms": round(float(np.mean(rms_vals)), 6) if rms_vals else None,
            "mean_spectral_centroid": (
                round(float(np.mean(cent_vals)), 2) if cent_vals else None
            ),
            "top_songs": [{"title": t, "count": c} for t, c in top_songs],
        }
    return types


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def _run(args: argparse.Namespace) -> tuple[Path, dict[int, dict]]:
    min_distance = max(1, int(args.min_passage_duration / SEGMENT_STEP_SECONDS))
    threshold_str = f"{args.peak_threshold}".replace(".", "p")
    run_name = args.name or f"ssm-k{args.kernel_size}-t{threshold_str}"
    out_dir = args.output_dir / run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("Output: %s", out_dir)

    db = SessionLocal()
    try:
        log.info("Loading segments with CLAP embeddings…")
        all_segments = _load_segments(db)
    finally:
        db.close()

    if not all_segments:
        log.error("No segments with CLAP embeddings — run extract_clap_embeddings.py first.")
        raise SystemExit(1)

    log.info("Loaded %d segments from DB", len(all_segments))

    by_recording: dict[int, list[dict]] = defaultdict(list)
    for seg in all_segments:
        by_recording[seg["recording_id"]].append(seg)
    for segs in by_recording.values():
        segs.sort(key=lambda s: s["start_seconds"])

    log.info("Processing %d recordings…", len(by_recording))

    all_passages: list[dict] = []
    all_passage_embeddings: list[np.ndarray] = []
    recording_stats: list[dict] = []
    recording_data: dict[int, dict] = {}
    passage_id_counter = 0

    for recording_id in sorted(by_recording):
        segs = by_recording[recording_id]
        n = len(segs)
        embeddings = np.stack([s["embedding"] for s in segs]).astype(np.float32)
        embeddings_normed = _l2_normalize(embeddings)
        ssm = (embeddings_normed @ embeddings_normed.T).clip(-1.0, 1.0)

        if n >= args.min_segments:
            novelty = _novelty_curve(ssm, args.kernel_size)
            boundaries = _pick_boundaries(novelty, args.peak_threshold, min_distance)
        else:
            novelty = np.zeros(n, dtype=np.float32)
            boundaries = []

        passages, passage_embs = _extract_passages(
            segs, embeddings_normed, boundaries, passage_id_counter
        )
        passage_id_counter += len(passages)
        all_passages.extend(passages)
        all_passage_embeddings.extend(passage_embs)

        recording_stats.append({
            "recording_id": recording_id,
            "recording_title": segs[0]["recording_title"],
            "n_segments": n,
            "n_passages": len(passages),
            "boundaries": boundaries,
            "novelty_mean": round(float(novelty.mean()), 4),
            "novelty_std": round(float(novelty.std()), 4),
            "novelty_max": round(float(novelty.max()), 4),
            "single_passage": n < args.min_segments,
        })

        recording_data[recording_id] = {
            "segs": segs,
            "ssm": ssm,
            "novelty": novelty,
            "passages": passages,
            "boundaries": boundaries,
        }

    log.info(
        "Extracted %d passages from %d recordings (mean %.1f per recording)",
        len(all_passages),
        len(by_recording),
        len(all_passages) / len(by_recording) if by_recording else 0,
    )

    config = {
        "method": "ssm",
        "run_name": run_name,
        "kernel_size": args.kernel_size,
        "peak_threshold": args.peak_threshold,
        "min_segments": args.min_segments,
        "min_passage_duration_seconds": args.min_passage_duration,
        "n_recordings": len(by_recording),
        "n_passages": len(all_passages),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "config.json").write_text(json.dumps(config, indent=2))

    (out_dir / "passages.json").write_text(json.dumps(all_passages, separators=(",", ":")))
    log.info("Passages → %s  (%d)", out_dir / "passages.json", len(all_passages))

    passage_types = _build_passage_types(all_passages)
    (out_dir / "passage_types.json").write_text(json.dumps(passage_types, indent=2))
    log.info("Passage types → %s  (%d types)", out_dir / "passage_types.json", len(passage_types))

    (out_dir / "recording_stats.json").write_text(json.dumps(recording_stats, indent=2))
    log.info("Recording stats → %s", out_dir / "recording_stats.json")

    if all_passage_embeddings:
        emb_matrix = np.stack(all_passage_embeddings).astype(np.float32)
        np.save(str(out_dir / "passage_embeddings.npy"), emb_matrix)
        passage_ids = [p["passage_id"] for p in all_passages]
        (out_dir / "passage_ids.json").write_text(json.dumps(passage_ids))
        log.info(
            "Passage embeddings → %s  shape=%s",
            out_dir / "passage_embeddings.npy",
            emb_matrix.shape,
        )

    _log_summary(recording_stats, all_passages)
    return out_dir, recording_data


def _log_summary(recording_stats: list[dict], passages: list[dict]) -> None:
    n_passages = [s["n_passages"] for s in recording_stats]
    durations = [p["duration"] for p in passages]
    log.info(
        "Passages per recording — mean: %.1f | median: %.1f | min: %d | max: %d",
        float(np.mean(n_passages)),
        float(np.median(n_passages)),
        int(min(n_passages)),
        int(max(n_passages)),
    )
    if durations:
        log.info(
            "Passage duration — mean: %.0fs | median: %.0fs | min: %.0fs | max: %.0fs",
            np.mean(durations),
            np.median(durations),
            min(durations),
            max(durations),
        )


# ---------------------------------------------------------------------------
# Explorer HTML generation
# ---------------------------------------------------------------------------

def _ssm_to_png_b64(ssm: np.ndarray, max_size: int = 200) -> str:
    """Downsample SSM and encode as base64 grayscale PNG (pure Python, no PIL)."""
    n = ssm.shape[0]
    if n > max_size:
        factor = n // max_size
        trimmed = n - (n % factor)
        mat = (
            ssm[:trimmed, :trimmed]
            .reshape(trimmed // factor, factor, trimmed // factor, factor)
            .mean(axis=(1, 3))
        )
    else:
        mat = ssm

    pixels = ((mat + 1.0) * 0.5 * 255.0).clip(0, 255).astype(np.uint8)
    h, w = pixels.shape

    def _chunk(name: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(name + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", crc)

    raw_rows = b"".join(b"\x00" + bytes(pixels[y]) for y in range(h))
    compressed = zlib.compress(raw_rows, level=6)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode("ascii")


def _novelty_svg(
    novelty: np.ndarray,
    boundaries: list[int],
    width: int = 560,
    height: int = 70,
) -> str:
    n = len(novelty)
    if n == 0:
        return f'<svg width="{width}" height="{height}"></svg>'

    lo, hi = float(novelty.min()), float(novelty.max())
    rng = hi - lo if hi > lo else 1.0

    def _x(i: int) -> float:
        return i / max(n - 1, 1) * width

    def _y(v: float) -> float:
        return height - 4 - (v - lo) / rng * (height - 8)

    points = " ".join(f"{_x(i):.1f},{_y(float(v)):.1f}" for i, v in enumerate(novelty))
    threshold_y = _y(float(novelty.mean() + novelty.std()))

    threshold_line = (
        f'<line x1="0" y1="{threshold_y:.1f}" x2="{width}" y2="{threshold_y:.1f}" '
        f'stroke="#e15759" stroke-width="1" stroke-dasharray="4,3" opacity="0.6"/>'
    )
    peak_marks = "".join(
        f'<line x1="{_x(b):.1f}" y1="0" x2="{_x(b):.1f}" y2="{height}" '
        f'stroke="#e15759" stroke-width="1.5" opacity="0.75"/>'
        for b in boundaries
    )
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<rect width="{width}" height="{height}" fill="#f5f5f5" rx="2"/>'
        f"{threshold_line}{peak_marks}"
        f'<polyline points="{points}" fill="none" stroke="#377eb8" stroke-width="1.5"/>'
        f"</svg>"
    )


_COLORS = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
    "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
    "#8cd17d", "#b6992d", "#f1ce63", "#a0cbe8", "#ffbe7d",
    "#d4a6c8", "#86bcb6", "#d37295", "#fabfd2", "#b9ac8e",
]


def _audio_url(audio_path: str | None, start: float, end: float, port: int | None) -> str | None:
    if not audio_path:
        return None
    if port is not None:
        return f"http://127.0.0.1:{port}/{audio_path}#t={start:.1f},{end:.1f}"
    processed_root = os.environ.get("PROCESSED_ROOT", "")
    if processed_root:
        return f"file://{Path(processed_root).expanduser() / audio_path}#t={start:.1f},{end:.1f}"
    return None


def _passage_timeline_html(passages: list[dict], port: int | None) -> str:
    if not passages:
        return ""
    total_dur = passages[-1]["end_seconds"] - passages[0]["start_seconds"]
    if total_dur <= 0:
        return ""

    blocks = []
    for p in passages:
        pct = p["duration"] / total_dur * 100
        color = _COLORS[p["passage_type"] % len(_COLORS)]
        label = f"P{p['passage_type']}" if pct > 6 else ""
        url = _audio_url(p.get("audio_path"), p["start_seconds"], p["end_seconds"], port)
        audio_tag = (
            f'<audio controls preload="none" src="{url}" '
            f'style="width:100%;height:28px;margin-top:4px"></audio>'
            if url
            else ""
        )
        t0, t1 = int(p["start_seconds"]), int(p["end_seconds"])
        title = p.get("song_title") or p.get("recording_title") or f"rec {p['recording_id']}"
        tooltip = f"P{p['passage_type']} · {t0}s–{t1}s · {p['segment_count']} seg"
        blocks.append(
            f'<div class="pb" style="width:{pct:.2f}%;background:{color}" '
            f'onclick="togglePb(this)" title="{tooltip}">'
            f'<span class="pl">{label}</span>'
            f'<div class="pd">'
            f'<div style="font-size:.75em;color:#444;padding:3px 0">{title} · {t0}s–{t1}s</div>'
            f"{audio_tag}"
            f"</div></div>"
        )
    return f'<div class="tl">{"".join(blocks)}</div>'


def _build_explorer_html(
    recording_data: dict[int, dict],
    out_dir: Path,
    port: int | None,
    n_recordings: int,
) -> Path:
    sorted_rids = sorted(
        recording_data,
        key=lambda rid: -len(recording_data[rid]["passages"]),
    )[:n_recordings]

    cfg = json.loads((out_dir / "config.json").read_text())

    css = """<style>
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:20px;background:#f8f9fa}
h1{font-size:1.3em;color:#333;margin-bottom:4px}
.meta{color:#666;font-size:.82em;margin-bottom:24px}
.rec{background:#fff;border:1px solid #ddd;border-radius:8px;margin-bottom:28px;padding:18px 20px}
.rh{font-size:1em;font-weight:600;color:#222;margin-bottom:2px}
.rm{font-size:.8em;color:#888;margin-bottom:12px}
.grid{display:grid;grid-template-columns:210px 1fr;gap:18px;align-items:start;margin-bottom:12px}
.lbl{font-size:.7em;color:#999;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}
img.ssm{width:200px;height:200px;image-rendering:pixelated;border:1px solid #eee;display:block}
.tl{display:flex;height:36px;border-radius:4px;overflow:visible;border:1px solid #ddd;cursor:pointer;margin-top:12px}
.pb{position:relative;transition:opacity .15s;overflow:hidden;flex-shrink:0}
.pb:hover{opacity:.85}
.pl{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:10px;color:#fff;font-weight:600;white-space:nowrap;pointer-events:none}
.pd{display:none;position:absolute;top:36px;left:0;z-index:10;min-width:220px;background:#fff;border:1px solid #ccc;border-radius:4px;padding:4px 8px;box-shadow:0 2px 8px rgba(0,0,0,.15)}
.pb.open{overflow:visible}
.pb.open .pd{display:block}
</style>
<script>
function togglePb(el){
  const wasOpen=el.classList.contains('open');
  el.closest('.tl').querySelectorAll('.pb.open').forEach(b=>b.classList.remove('open'));
  if(!wasOpen)el.classList.add('open');
}
document.addEventListener('click',e=>{
  if(!e.target.closest('.pb'))document.querySelectorAll('.pb.open').forEach(b=>b.classList.remove('open'));
});
</script>"""

    blocks = []
    for rid in sorted_rids:
        d = recording_data[rid]
        segs, ssm, novelty = d["segs"], d["ssm"], d["novelty"]
        passages, boundaries = d["passages"], d["boundaries"]

        title = segs[0].get("song_title") or segs[0].get("recording_title") or f"Recording {rid}"
        date = segs[0].get("session_date") or ""
        etype = segs[0].get("effective_type") or ""
        total_dur = passages[-1]["end_seconds"] if passages else 0

        ssm_b64 = _ssm_to_png_b64(ssm)
        novelty_svg = _novelty_svg(novelty, boundaries)
        timeline_html = _passage_timeline_html(passages, port)

        meta_parts = [p for p in [date, etype] if p]
        meta_parts += [
            f"{len(segs)} segments",
            f"{len(passages)} passages",
            f"{total_dur:.0f}s",
        ]
        blocks.append(
            f'<div class="rec">'
            f'<div class="rh">{title}</div>'
            f'<div class="rm">{" · ".join(meta_parts)}</div>'
            f'<div class="grid">'
            f'<div><div class="lbl">Self-Similarity Matrix</div>'
            f'<img class="ssm" src="data:image/png;base64,{ssm_b64}" alt="SSM"/></div>'
            f'<div>'
            f'<div class="lbl">Novelty curve (red = peak boundaries)</div>'
            f"{novelty_svg}"
            f'<div class="lbl" style="margin-top:10px">Passages (click to expand audio)</div>'
            f"{timeline_html}"
            f"</div></div></div>"
        )

    html = (
        f"<!DOCTYPE html><html lang='en'>"
        f"<head><meta charset='UTF-8'>"
        f"<title>SSM Explorer — {cfg['run_name']}</title>"
        f"{css}</head><body>"
        f"<h1>SSM Explorer — {cfg['run_name']}</h1>"
        f"<div class='meta'>"
        f"kernel_size: <strong>{cfg['kernel_size']}</strong> · "
        f"peak_threshold: <strong>{cfg['peak_threshold']}</strong> · "
        f"min_passage_duration: <strong>{cfg['min_passage_duration_seconds']}s</strong> · "
        f"recordings shown: <strong>{len(sorted_rids)}</strong> (most-segmented) · "
        f"total passages: <strong>{cfg['n_passages']}</strong>"
        f"</div>"
        f"{''.join(blocks)}"
        f"</body></html>"
    )

    out_path = out_dir / "ssm_explorer.html"
    out_path.write_text(html)
    log.info("Explorer → %s", out_path.resolve())
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Per-recording passage segmentation via SSM + novelty detection (Foote 2000).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--kernel-size", type=int, default=8, metavar="N",
        help="Checkerboard kernel size in segments (default: 8)",
    )
    p.add_argument(
        "--peak-threshold", type=float, default=1.0, metavar="F",
        help="Novelty peak height = mean + F*std (default: 1.0)",
    )
    p.add_argument(
        "--min-segments", type=int, default=6, metavar="N",
        help="Min segments to attempt segmentation; fewer → single passage (default: 6)",
    )
    p.add_argument(
        "--min-passage-duration", type=float, default=30.0, metavar="F",
        help="Minimum passage duration in seconds, controls peak min_distance (default: 30.0)",
    )
    p.add_argument(
        "--name", default=None,
        help="Run name / output subdirectory (default: auto, e.g. ssm-k8-t1.0)",
    )
    p.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, metavar="DIR",
        help=f"Root output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    p.add_argument(
        "--explore", action="store_true",
        help="Generate HTML explorer, start servers, and open in browser",
    )
    p.add_argument(
        "--explore-n", type=int, default=20, metavar="N",
        help="Max recordings to include in explorer, by passage count (default: 20)",
    )
    p.add_argument(
        "--port", type=int, default=8765, metavar="PORT",
        help="Preferred audio server port (default: 8765; next free port chosen if busy)",
    )
    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = _parse_args()
    out_dir, recording_data = _run(args)

    if args.explore:
        import webbrowser

        from pipeline.scripts.explore_clusters import (
            block_until_interrupt,
            start_servers,
        )

        audio_srv, html_srv, audio_port, html_port = start_servers(out_dir, args.port)
        explorer_path = _build_explorer_html(recording_data, out_dir, audio_port, args.explore_n)
        rel = explorer_path.relative_to(out_dir)
        webbrowser.open(f"http://127.0.0.1:{html_port}/{rel}")
        block_until_interrupt(audio_srv, html_srv, audio_port=audio_port, html_port=html_port)
    else:
        log.info("Done. Run with --explore to generate the visual explorer.")


if __name__ == "__main__":
    main()
