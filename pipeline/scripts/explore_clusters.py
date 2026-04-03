"""Generate visual and auditory explorations from clustering results.

Reads a clustering run from data/eternal-rehearsal/<run_name>/ and produces:
  1. umap_clusters.html — interactive Plotly scatter with cluster coloring
     alongside effective_type coloring (two subplots) for comparison.
  2. cluster_sampler.html — self-contained HTML with inline <audio> players;
     for each cluster, 5–8 random segments playable via media fragment URIs.

Both files are written to the run directory (alongside assignments.json, etc.).

Usage:
  python -m pipeline.scripts.explore_clusters [options]

Options:
  --run NAME          Clustering run name (subdirectory under --cluster-dir).
                      Default: most-recently-modified run.
  --all               Generate explorers for all runs under --cluster-dir and
                      write an index.html linking them all.
  --cluster-dir DIR   Parent directory of clustering runs (default: data/eternal-rehearsal)
  --umap-file FILE    UMAP JSON to use for coordinates (default: data/umaps/segments/all.json)
  --n-samples N       Segments per cluster in sampler (default: 10)
  --serve             Start a local HTTP server for audio (supports Range requests for
                      seeking). Audio URLs use http://127.0.0.1:PORT/ instead of file://.
  --port PORT         Preferred port for the audio server (default: 8765). A nearby free
                      port is chosen automatically if the preferred one is busy.
  --open              Open the generated HTML in the browser. Enabled automatically with
                      --serve.
"""

import argparse
import http.server
import json
import logging
import math
import mimetypes
import os
import random
import re
import socket
import threading
import webbrowser
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_CLUSTER_DIR = Path("data/eternal-rehearsal")
DEFAULT_UMAP_FILE = Path("data/umaps/segments/all.json")
DEFAULT_PORT = 8765

EFFECTIVE_TYPE_COLORS = {
    "original": "#4e79a7",
    "cover": "#f28e2b",
    "jam": "#59a14f",
    "non-musical": "#e15759",
    "unreviewed": "#bab0ac",
}

CLUSTER_PALETTE = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00",
    "#a65628", "#f781bf", "#999999", "#66c2a5", "#fc8d62",
    "#8da0cb", "#e78ac3", "#a6d854", "#ffd92f", "#e5c494",
    "#b3b3b3", "#1b9e77", "#d95f02", "#7570b3", "#e7298a",
]
NOISE_COLOR = "#dddddd"

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTTP audio server
# ---------------------------------------------------------------------------

def _find_free_port(start: int = DEFAULT_PORT, attempts: int = 20) -> int:
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port found in range {start}–{start + attempts - 1}")


class _RangeRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler with Range request support for audio seeking."""

    def do_GET(self) -> None:
        range_header = self.headers.get("Range")
        if range_header:
            path = self.translate_path(self.path.split("?")[0])
            if os.path.isfile(path):
                self._serve_range(path, range_header)
                return
        super().do_GET()

    def _serve_range(self, path: str, range_header: str) -> None:
        file_size = os.path.getsize(path)
        m = re.match(r"bytes=(\d*)-(\d*)", range_header)
        if not m:
            super().do_GET()
            return
        start = int(m.group(1)) if m.group(1) else 0
        end = int(m.group(2)) if m.group(2) else file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        self.send_response(206)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def end_headers(self) -> None:
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


def _make_handler_cls(directory: Path) -> type:
    class _Handler(_RangeRequestHandler):
        def __init__(self, *a: object, **kw: object) -> None:
            super().__init__(*a, directory=str(directory), **kw)
    return _Handler


def start_server(
    directory: Path, preferred_port: int = DEFAULT_PORT
) -> tuple[http.server.HTTPServer, int]:
    """Start a background HTTP file server with Range support. Returns (server, port)."""
    port = _find_free_port(preferred_port)
    server = http.server.HTTPServer(("127.0.0.1", port), _make_handler_cls(directory))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("http://127.0.0.1:%d  ← %s", port, directory)
    return server, port


def start_servers(
    cluster_dir: Path,
    preferred_port: int = DEFAULT_PORT,
) -> tuple[http.server.HTTPServer, http.server.HTTPServer, int, int]:
    """Start audio + HTML servers. Returns (audio_server, html_server, audio_port, html_port).

    The audio server serves PROCESSED_ROOT (for <audio> src URLs baked into sampler HTML).
    The HTML server serves cluster_dir so samplers open as proper http:// pages.
    A nearby free port is chosen automatically if the preferred one is busy.
    """
    processed_root = os.environ.get("PROCESSED_ROOT", "")
    if not processed_root:
        from pipeline.config import PROCESSED_ROOT as pr  # noqa: PLC0415
        processed_root = str(pr)
    audio_server, audio_port = start_server(Path(processed_root).expanduser(), preferred_port)
    html_server, html_port = start_server(cluster_dir.resolve(), audio_port + 1)
    return audio_server, html_server, audio_port, html_port


def block_until_interrupt(
    *servers: http.server.HTTPServer,
    audio_port: int,
    html_port: int,
) -> None:
    """Log server URLs and block until Ctrl+C, then shut down all servers."""
    log.info(
        "Audio → http://127.0.0.1:%d | HTML → http://127.0.0.1:%d — Ctrl+C to stop.",
        audio_port,
        html_port,
    )
    import time  # noqa: PLC0415
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        for server in servers:
            server.shutdown()
        log.info("Stopped.")


# ---------------------------------------------------------------------------
# Run / data loading
# ---------------------------------------------------------------------------

def _find_run_dir(cluster_dir: Path, run_name: str | None) -> Path:
    if run_name:
        candidate = cluster_dir / run_name
        if not candidate.is_dir():
            raise FileNotFoundError(f"Run directory not found: {candidate}")
        return candidate

    candidates = [
        d for d in cluster_dir.iterdir()
        if d.is_dir() and (d / "assignments.json").exists()
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No clustering runs found under {cluster_dir}. "
            "Run cluster_segments.py first."
        )
    return max(candidates, key=lambda d: d.stat().st_mtime)


def _find_all_run_dirs(cluster_dir: Path) -> list[Path]:
    if not cluster_dir.is_dir():
        return []
    return sorted(
        [d for d in cluster_dir.iterdir() if d.is_dir() and (d / "assignments.json").exists()],
        key=lambda d: d.stat().st_mtime,
    )


def _load_assignments(run_dir: Path) -> tuple[list[dict], dict]:
    assignments = json.loads((run_dir / "assignments.json").read_text())
    by_segment_id = {a["segment_id"]: a for a in assignments}
    return assignments, by_segment_id


def _load_config(run_dir: Path) -> dict:
    return json.loads((run_dir / "config.json").read_text())


def _load_umap(umap_file: Path) -> dict[int, dict]:
    points = json.loads(umap_file.read_text())
    return {p["segment_id"]: p for p in points}


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def _cluster_color(cluster_id: int) -> str:
    if cluster_id == -1:
        return NOISE_COLOR
    return CLUSTER_PALETTE[cluster_id % len(CLUSTER_PALETTE)]


# ---------------------------------------------------------------------------
# UMAP HTML
# ---------------------------------------------------------------------------

def _build_umap_html(
    assignments: list[dict],
    umap_by_id: dict[int, dict],
    config: dict,
    stats: dict,
    run_dir: Path,
) -> None:
    import plotly.graph_objects as go  # noqa: PLC0415
    from plotly.subplots import make_subplots  # noqa: PLC0415

    joined = []
    missing_umap = 0
    for a in assignments:
        pt = umap_by_id.get(a["segment_id"])
        if pt is None:
            missing_umap += 1
            continue
        joined.append({**a, "x": pt["x"], "y": pt["y"]})

    if missing_umap:
        log.warning("%d segments missing UMAP coordinates — skipped in scatter.", missing_umap)

    if not joined:
        log.error("No segments with both cluster assignments and UMAP coords — cannot build UMAP plot.")
        return

    cluster_ids_all = sorted(set(a["cluster_id"] for a in joined))
    effective_types_all = sorted(set(a["effective_type"] for a in joined))

    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=[
            f"By cluster (n={config.get('n_clusters', config.get('n_states', '?'))})",
            "By effective type",
        ],
        horizontal_spacing=0.06,
    )

    for cid in cluster_ids_all:
        pts = [a for a in joined if a["cluster_id"] == cid]
        label = "noise" if cid == -1 else f"cluster {cid}"
        color = _cluster_color(cid)
        hover = [
            f"cluster: {cid}<br>"
            f"type: {p['effective_type']}<br>"
            f"recording: {p['recording_title'] or '?'}<br>"
            f"date: {p['session_date'] or '?'}<br>"
            f"start: {p['start_seconds']:.0f}s"
            for p in pts
        ]
        trace = go.Scattergl(
            x=[p["x"] for p in pts],
            y=[p["y"] for p in pts],
            mode="markers",
            name=label,
            marker=dict(color=color, size=3, opacity=0.7),
            hovertext=hover,
            hoverinfo="text",
            legendgroup=f"cluster_{cid}",
        )
        fig.add_trace(trace, row=1, col=1)

    for etype in effective_types_all:
        pts = [a for a in joined if a["effective_type"] == etype]
        color = EFFECTIVE_TYPE_COLORS.get(etype, "#aaaaaa")
        hover = [
            f"type: {p['effective_type']}<br>"
            f"cluster: {p['cluster_id']}<br>"
            f"recording: {p['recording_title'] or '?'}<br>"
            f"date: {p['session_date'] or '?'}"
            for p in pts
        ]
        trace = go.Scattergl(
            x=[p["x"] for p in pts],
            y=[p["y"] for p in pts],
            mode="markers",
            name=etype,
            marker=dict(color=color, size=3, opacity=0.7),
            hovertext=hover,
            hoverinfo="text",
            legendgroup=f"etype_{etype}",
            showlegend=True,
        )
        fig.add_trace(trace, row=1, col=2)

    noise_frac = stats.get("noise_fraction", 0)
    features_str = config.get("features", config.get("feature_source", "?"))
    if "min_cluster_size" in config:
        key_param = f"min_cluster_size: {config['min_cluster_size']}"
    elif "n_states" in config:
        key_param = f"n_states: {config['n_states']}"
    else:
        key_param = f"n_clusters: {config.get('n_clusters', '?')}"
    fig.update_layout(
        title=dict(
            text=(
                f"Segment clustering — run: {config['run_name']} | "
                f"features: {features_str} | "
                f"{key_param} | "
                f"noise: {noise_frac * 100:.1f}%"
            ),
            font=dict(size=13),
        ),
        height=700,
        template="plotly_white",
        legend=dict(itemsizing="constant"),
    )
    fig.update_xaxes(showticklabels=False)
    fig.update_yaxes(showticklabels=False)

    out_path = run_dir / "umap_clusters.html"
    fig.write_html(str(out_path), include_plotlyjs="cdn")
    log.info("UMAP plot written → %s", out_path.resolve())


# ---------------------------------------------------------------------------
# Audio URL
# ---------------------------------------------------------------------------

def _audio_url(
    audio_path: str | None,
    start: float,
    end: float,
    port: int | None = None,
) -> str | None:
    if not audio_path:
        return None
    if port is not None:
        return f"http://127.0.0.1:{port}/{audio_path}#t={start:.1f},{end:.1f}"
    processed_root = os.environ.get("PROCESSED_ROOT", "")
    if not processed_root:
        from pipeline.config import PROCESSED_ROOT as pr  # noqa: PLC0415
        processed_root = str(pr)
    abs_path = Path(processed_root).expanduser() / audio_path
    return f"file://{abs_path}#t={start:.1f},{end:.1f}"


# ---------------------------------------------------------------------------
# Cluster acoustic profile (B1)
# ---------------------------------------------------------------------------

def _compute_cluster_acoustics(
    by_cluster: dict[int, list[dict]],
) -> dict[int, dict[str, float | None]]:
    import numpy as np  # noqa: PLC0415

    result: dict[int, dict[str, float | None]] = {}
    for cid, segs in by_cluster.items():
        if cid == -1:
            continue
        rms_vals = [s["mean_rms"] for s in segs if s.get("mean_rms") is not None]
        cent_vals = [
            s["mean_spectral_centroid"]
            for s in segs
            if s.get("mean_spectral_centroid") is not None
        ]
        result[cid] = {
            "mean_rms": float(np.mean(rms_vals)) if rms_vals else None,
            "mean_centroid": float(np.mean(cent_vals)) if cent_vals else None,
        }
    return result


def _normalize_metric(
    acoustics: dict[int, dict[str, float | None]], key: str
) -> dict[int, float | None]:
    vals = {cid: v[key] for cid, v in acoustics.items() if v.get(key) is not None}
    if not vals:
        return {cid: None for cid in acoustics}
    lo, hi = min(vals.values()), max(vals.values())
    out: dict[int, float | None] = {}
    for cid in acoustics:
        v = vals.get(cid)
        if v is None:
            out[cid] = None
        elif hi == lo:
            out[cid] = 50.0
        else:
            out[cid] = (v - lo) / (hi - lo) * 100.0
    return out


def _level_label(pct: float | None, low: str, mid: str, high: str) -> str:
    if pct is None:
        return ""
    return high if pct >= 67 else (low if pct <= 33 else mid)


def _cluster_card_html(
    acoustics: dict[str, float | None],
    rms_pct: float | None,
    centroid_pct: float | None,
    dominant_type: str,
) -> str:
    has_rms = acoustics.get("mean_rms") is not None
    has_centroid = acoustics.get("mean_centroid") is not None
    if not has_rms and not has_centroid:
        return ""

    parts = []
    if has_rms and rms_pct is not None:
        parts.append(_level_label(rms_pct, "quiet", "mid-level", "loud"))
    if has_centroid and centroid_pct is not None:
        parts.append(_level_label(centroid_pct, "dark", "neutral", "bright"))
    if dominant_type:
        parts.append(f"mostly {dominant_type}")
    label = " · ".join(p for p in parts if p)

    rms_bar = ""
    if has_rms and rms_pct is not None:
        mean_rms = acoustics["mean_rms"]
        rms_db_str = f"{20 * math.log10(mean_rms):.1f} dB" if mean_rms and mean_rms > 0 else "N/A"
        rms_bar = (
            f'<div class="card-metric">'
            f'<span class="card-metric-label">Volume</span>'
            f'<div class="card-bar-track"><div class="card-bar-fill" style="width:{rms_pct:.0f}%"></div></div>'
            f'<span class="card-metric-value">{rms_db_str}</span>'
            f"</div>"
        )

    centroid_bar = ""
    if has_centroid and centroid_pct is not None:
        mean_centroid = acoustics["mean_centroid"]
        cent_str = f"{mean_centroid / 1000:.1f} kHz" if mean_centroid else "N/A"
        centroid_bar = (
            f'<div class="card-metric">'
            f'<span class="card-metric-label">Brightness</span>'
            f'<div class="card-bar-track"><div class="card-bar-fill card-bar-brightness" style="width:{centroid_pct:.0f}%"></div></div>'
            f'<span class="card-metric-value">{cent_str}</span>'
            f"</div>"
        )

    return (
        f'<div class="cluster-card">'
        f'<div class="card-metrics">{rms_bar}{centroid_bar}</div>'
        f'<div class="cluster-label">{label}</div>'
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Sampler HTML
# ---------------------------------------------------------------------------

def _build_sampler_html(
    assignments: list[dict],
    config: dict,
    stats: dict,
    n_samples: int,
    run_dir: Path,
    port: int | None = None,
) -> None:
    cluster_ids = sorted(set(a["cluster_id"] for a in assignments))
    by_cluster: dict[int, list[dict]] = {}
    for a in assignments:
        by_cluster.setdefault(a["cluster_id"], []).append(a)

    acoustics = _compute_cluster_acoustics(by_cluster)
    rms_pcts = _normalize_metric(acoustics, "mean_rms")
    centroid_pcts = _normalize_metric(acoustics, "mean_centroid")

    css = """
    <style>
    body { font-family: system-ui, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f8f9fa; }
    h1 { font-size: 1.4em; color: #333; }
    .meta { color: #666; font-size: 0.85em; margin-bottom: 24px; }
    .cluster { background: white; border: 1px solid #ddd; border-radius: 8px; margin-bottom: 20px; padding: 16px 20px; }
    .cluster-header { display: flex; align-items: baseline; gap: 12px; margin-bottom: 12px; }
    .cluster-id { font-size: 1.1em; font-weight: 600; color: #222; }
    .cluster-count { font-size: 0.85em; color: #888; }
    .cluster-purity { font-size: 0.8em; color: #555; background: #f0f0f0; padding: 2px 8px; border-radius: 4px; }
    .cluster-card { margin-bottom: 12px; padding: 10px 12px; background: #f8f9fa; border-radius: 6px; border: 1px solid #e9ecef; }
    .card-metrics { display: flex; flex-direction: column; gap: 6px; margin-bottom: 6px; }
    .card-metric { display: flex; align-items: center; gap: 8px; }
    .card-metric-label { font-size: 0.73em; color: #888; width: 58px; flex-shrink: 0; }
    .card-bar-track { flex: 1; height: 5px; background: #e2e6ea; border-radius: 3px; overflow: hidden; }
    .card-bar-fill { height: 100%; background: #6c9fd4; border-radius: 3px; }
    .card-bar-brightness { background: #e8a04a; }
    .card-metric-value { font-size: 0.73em; color: #666; width: 52px; text-align: right; flex-shrink: 0; }
    .cluster-label { font-size: 0.78em; color: #777; font-style: italic; }
    .segment { border-top: 1px solid #f0f0f0; padding: 10px 0; display: flex; flex-direction: column; gap: 4px; }
    .segment-meta { font-size: 0.8em; color: #666; }
    .segment-type { font-size: 0.75em; padding: 1px 6px; border-radius: 3px; display: inline-block; }
    audio { width: 100%; height: 32px; margin-top: 4px; }
    .noise { border-left: 4px solid #ccc; }
    </style>
    """

    def _purity_str(cluster_id: int) -> str:
        purity = stats.get("purity_by_cluster", {}).get(str(cluster_id), {})
        if not purity:
            return ""
        dominant = max(purity, key=purity.get)  # type: ignore[arg-type]
        total = sum(purity.values())
        pct = round(purity[dominant] / total * 100)
        return f"{dominant}: {pct}%"

    def _dominant_type(cluster_id: int) -> str:
        purity = stats.get("purity_by_cluster", {}).get(str(cluster_id), {})
        if not purity:
            return ""
        return max(purity, key=purity.get)  # type: ignore[arg-type]

    def _type_badge(etype: str) -> str:
        colors = {
            "original": "#d4e6f1",
            "cover": "#fde8c8",
            "jam": "#d5f0d5",
            "non-musical": "#fad4d4",
            "unreviewed": "#e8e8e8",
        }
        bg = colors.get(etype, "#e8e8e8")
        return f'<span class="segment-type" style="background:{bg}">{etype}</span>'

    random.seed(42)
    blocks = []
    for cid in cluster_ids:
        segments = by_cluster.get(cid, [])
        sample = random.sample(segments, min(n_samples, len(segments)))
        label = "noise (-1)" if cid == -1 else f"cluster {cid}"
        size = stats.get("cluster_sizes", {}).get(str(cid), len(segments))
        purity = _purity_str(cid)
        cls = "cluster noise" if cid == -1 else "cluster"

        card_html = ""
        if cid != -1 and cid in acoustics:
            card_html = _cluster_card_html(
                acoustics[cid],
                rms_pcts.get(cid),
                centroid_pcts.get(cid),
                _dominant_type(cid),
            )

        seg_blocks = []
        for seg in sample:
            url = _audio_url(seg.get("audio_path"), seg["start_seconds"], seg["end_seconds"], port)
            title = seg.get("recording_title") or "?"
            date = seg.get("session_date") or "?"
            song = seg.get("song_title")
            song_str = f" · {song}" if song else ""
            etype = seg.get("effective_type", "unreviewed")
            t_start = int(seg["start_seconds"])
            t_end = int(seg["end_seconds"])
            audio_tag = (
                f'<audio controls preload="none" src="{url}"></audio>'
                if url else '<em style="color:#aaa">no audio</em>'
            )
            seg_blocks.append(
                f'<div class="segment">'
                f'<div class="segment-meta">{date}{song_str} — {title} '
                f'[{t_start}s–{t_end}s] {_type_badge(etype)}</div>'
                f'{audio_tag}'
                f'</div>'
            )

        blocks.append(
            f'<div class="{cls}">'
            f'<div class="cluster-header">'
            f'<span class="cluster-id">{label}</span>'
            f'<span class="cluster-count">{size} segments</span>'
            f'{"<span class=cluster-purity>" + purity + "</span>" if purity else ""}'
            f'</div>'
            + card_html
            + "".join(seg_blocks)
            + "</div>"
        )

    noise_frac = stats.get("noise_fraction", 0)
    n_clusters = config.get("n_clusters", config.get("n_states", "?"))
    features_str = config.get("features", config.get("feature_source", "?"))
    if "min_cluster_size" in config:
        key_param_html = f"min_cluster_size: <strong>{config['min_cluster_size']}</strong> ·"
    elif "n_states" in config:
        key_param_html = f"n_states: <strong>{config['n_states']}</strong> ·"
    else:
        key_param_html = f"n_clusters: <strong>{config.get('n_clusters', '?')}</strong> ·"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Cluster sampler — {config['run_name']}</title>
{css}
</head>
<body>
<h1>Cluster sampler — {config['run_name']}</h1>
<div class="meta">
  features: <strong>{features_str}</strong> ·
  {key_param_html}
  clusters: <strong>{n_clusters}</strong> ·
  noise: <strong>{noise_frac * 100:.1f}%</strong> ·
  samples/cluster: <strong>{n_samples}</strong>
</div>
{"".join(blocks)}
</body>
</html>"""

    out_path = run_dir / "cluster_sampler.html"
    out_path.write_text(html)
    audio_mode = f"http://127.0.0.1:{port}/" if port is not None else "file://"
    log.info("Cluster sampler written → %s  (audio via %s)", out_path.resolve(), audio_mode)


# ---------------------------------------------------------------------------
# Quantitative summary
# ---------------------------------------------------------------------------

def _print_quantitative(stats: dict, config: dict) -> None:
    n_clusters = stats.get("n_clusters", stats.get("n_states", "?"))
    noise_frac = stats["noise_fraction"]
    log.info(
        "=== Quantitative summary: %s ===",
        config["run_name"],
    )
    n_segments = stats.get(
        "n_segments",
        sum(stats["cluster_sizes"].values()) + stats.get("noise_count", 0),
    )
    log.info(
        "Segments: %d | Clusters: %d | Noise: %d (%.1f%%)",
        n_segments,
        n_clusters,
        stats.get("noise_count", 0),
        noise_frac * 100,
    )
    sizes = list(stats["cluster_sizes"].values())
    if sizes:
        import numpy as np  # noqa: PLC0415
        log.info(
            "Size — mean: %.0f | median: %.0f | min: %d | max: %d",
            float(np.mean(sizes)),
            float(np.median(sizes)),
            min(sizes),
            max(sizes),
        )

    purity = stats.get("purity_by_cluster", {})
    if purity:
        log.info("--- Purity by cluster (dominant effective_type) ---")
        for cid, counts in sorted(purity.items(), key=lambda x: int(x[0])):
            total = sum(counts.values())
            dominant = max(counts, key=counts.get)  # type: ignore[arg-type]
            pct = counts[dominant] / total * 100
            breakdown = " | ".join(f"{t}: {n}" for t, n in sorted(counts.items(), key=lambda x: -x[1]))
            log.info("  cluster %s (%d): %s %.0f%%  [%s]", cid, total, dominant, pct, breakdown)


# ---------------------------------------------------------------------------
# High-level generation (importable by cluster_segments --explore)
# ---------------------------------------------------------------------------

def generate_for_run(
    run_dir: Path,
    umap_file: Path = DEFAULT_UMAP_FILE,
    n_samples: int = 10,
    port: int | None = None,
) -> tuple[dict, dict]:
    """Generate explorer HTMLs for a single run. Returns (config, stats)."""
    assignments, _ = _load_assignments(run_dir)
    config = _load_config(run_dir)
    stats = json.loads((run_dir / "stats.json").read_text())

    log.info(
        "Run: %s — %d assignments, %d clusters, %.1f%% noise",
        run_dir.name,
        len(assignments),
        config.get("n_clusters", stats.get("n_clusters", stats.get("n_states", "?"))),
        stats["noise_fraction"] * 100,
    )

    _print_quantitative(stats, config)

    if umap_file.exists():
        umap_by_id = _load_umap(umap_file)
        _build_umap_html(assignments, umap_by_id, config, stats, run_dir)
    else:
        log.warning("UMAP file not found (%s) — skipping UMAP plot.", umap_file)

    _build_sampler_html(assignments, config, stats, n_samples, run_dir, port)
    return config, stats


def generate_all(
    cluster_dir: Path,
    umap_file: Path = DEFAULT_UMAP_FILE,
    n_samples: int = 10,
    port: int | None = None,
    run_dirs: list[Path] | None = None,
) -> list[tuple[Path, dict, dict]]:
    """Generate explorer HTMLs for runs under cluster_dir.

    If run_dirs is given, only those specific dirs are processed (used by
    cluster_segments --explore to avoid picking up unrelated HMM/other runs).
    Otherwise all dirs with assignments.json under cluster_dir are processed.
    Returns [(run_dir, config, stats)].
    """
    if run_dirs is None:
        run_dirs = _find_all_run_dirs(cluster_dir)
    if not run_dirs:
        log.warning("No clustering runs found under %s.", cluster_dir)
        return []

    results: list[tuple[Path, dict, dict]] = []
    for i, run_dir in enumerate(run_dirs, 1):
        log.info("[%d/%d] Generating explorer for %s…", i, len(run_dirs), run_dir.name)
        try:
            config, stats = generate_for_run(run_dir, umap_file, n_samples, port)
            results.append((run_dir, config, stats))
        except Exception as exc:
            log.error("Skipping %s: %s", run_dir.name, exc)

    _build_index_html(cluster_dir, results)
    return results


def _build_index_html(cluster_dir: Path, runs: list[tuple[Path, dict, dict]]) -> None:
    if not runs:
        return

    def _sort_key(item: tuple[Path, dict, dict]) -> tuple:
        _, cfg, _ = item
        return (cfg.get("method", ""), cfg.get("n_clusters", 0))

    rows_html = ""
    for run_dir, config, stats in sorted(runs, key=_sort_key):
        method = config.get("method", "?")
        n_clusters = config.get("n_clusters", stats.get("n_clusters", "?"))
        noise_pct = f"{stats.get('noise_fraction', 0) * 100:.1f}%"
        sampler_link = f'<a href="{run_dir.name}/cluster_sampler.html">{run_dir.name}</a>'
        umap_link = (
            f'<a href="{run_dir.name}/umap_clusters.html">UMAP</a>'
            if (run_dir / "umap_clusters.html").exists()
            else "—"
        )
        rows_html += (
            f"<tr>"
            f"<td>{sampler_link}</td>"
            f"<td>{method}</td>"
            f"<td>{n_clusters}</td>"
            f"<td>{noise_pct}</td>"
            f"<td>{umap_link}</td>"
            f"</tr>\n"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Clustering runs — {cluster_dir.name}</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 40px auto; padding: 20px; background: #f8f9fa; }}
h1 {{ font-size: 1.4em; color: #333; margin-bottom: 20px; }}
table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px;
         overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
th {{ background: #f0f0f0; padding: 10px 14px; text-align: left; font-size: 0.85em;
      color: #555; border-bottom: 2px solid #ddd; }}
td {{ padding: 10px 14px; font-size: 0.9em; border-bottom: 1px solid #f0f0f0; }}
tr:last-child td {{ border-bottom: none; }}
a {{ color: #377eb8; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>Clustering runs — {cluster_dir.name}</h1>
<table>
<thead>
<tr><th>Run</th><th>Method</th><th>Clusters</th><th>Noise</th><th>UMAP</th></tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
</body>
</html>"""

    out_path = cluster_dir / "index.html"
    out_path.write_text(html)
    log.info("Index written → %s", out_path.resolve())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate visual and auditory cluster explorations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--run",
        default=None,
        metavar="NAME",
        help="Clustering run name (default: most recently modified run)",
    )
    p.add_argument(
        "--all",
        action="store_true",
        dest="all_runs",
        help="Generate explorers for all runs under --cluster-dir and write index.html.",
    )
    p.add_argument(
        "--cluster-dir",
        type=Path,
        default=DEFAULT_CLUSTER_DIR,
        help=f"Parent directory of clustering runs (default: {DEFAULT_CLUSTER_DIR})",
    )
    p.add_argument(
        "--umap-file",
        type=Path,
        default=DEFAULT_UMAP_FILE,
        help=f"UMAP JSON file with x/y coords (default: {DEFAULT_UMAP_FILE})",
    )
    p.add_argument(
        "--n-samples",
        type=int,
        default=10,
        help="Segments per cluster in sampler (default: 10)",
    )
    p.add_argument(
        "--serve",
        action="store_true",
        help=(
            "Generate HTML then start audio + HTML servers and open the browser. "
            "Audio URLs are baked in as http://127.0.0.1:PORT/ for range-request seeking."
        ),
    )
    p.add_argument(
        "--serve-only",
        action="store_true",
        help=(
            "Start servers and open the browser without regenerating HTML. "
            "Use after a previous --serve run when you only want to re-open existing files."
        ),
    )
    p.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        metavar="PORT",
        help=(
            f"Preferred starting port (default: {DEFAULT_PORT}). "
            "A nearby free port is chosen automatically if this one is busy. "
            "The HTML server uses the next free port after the audio server."
        ),
    )
    p.add_argument(
        "--open",
        action="store_true",
        dest="open_browser",
        help="Open the generated HTML in the browser without starting servers.",
    )
    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = _parse_args()

    will_serve = args.serve or args.serve_only
    audio_server: http.server.HTTPServer | None = None
    html_server: http.server.HTTPServer | None = None
    audio_port: int | None = None
    html_port: int | None = None

    if will_serve:
        audio_server, html_server, audio_port, html_port = start_servers(
            args.cluster_dir, args.port
        )

    # Resolve run dir unconditionally so we have it for URL construction even with --serve-only.
    run_dir: Path | None = None
    if not args.all_runs:
        run_dir = _find_run_dir(args.cluster_dir, args.run)

    if not args.serve_only:
        if args.all_runs:
            generate_all(args.cluster_dir, args.umap_file, args.n_samples, audio_port)
        else:
            assert run_dir is not None
            log.info("Using run: %s", run_dir)
            generate_for_run(run_dir, args.umap_file, args.n_samples, audio_port)

    if will_serve or args.open_browser:
        if html_port is not None:
            if args.all_runs or args.serve_only and run_dir is None:
                browser_url = f"http://127.0.0.1:{html_port}/index.html"
            else:
                assert run_dir is not None
                rel = run_dir.resolve().relative_to(args.cluster_dir.resolve())
                browser_url = f"http://127.0.0.1:{html_port}/{rel}/cluster_sampler.html"
            webbrowser.open(browser_url)
        else:
            # --open without --serve: open file:// directly
            fallback = (
                args.cluster_dir / "index.html"
                if args.all_runs
                else (run_dir / "cluster_sampler.html" if run_dir else args.cluster_dir / "index.html")
            )
            if fallback.exists():
                webbrowser.open(f"file://{fallback.resolve()}")

    if will_serve:
        assert audio_server is not None and html_server is not None
        block_until_interrupt(audio_server, html_server, audio_port=audio_port, html_port=html_port)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
