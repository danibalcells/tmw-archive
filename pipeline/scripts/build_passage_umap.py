"""Build passage-level UMAP projections for the Recording Passage Mood Map.

For each recording, groups consecutive segments into acoustically coherent passages
using cosine-distance change-point detection on CLAP embeddings. Each passage is
represented by the mean UMAP coordinates of its constituent segments (read from an
existing segment-level UMAP JSON), plus averaged feature values.

Usage:
  python -m pipeline.scripts.build_passage_umap SOURCE_UMAP [options]

Arguments:
  SOURCE_UMAP              Path to a segment-level UMAP JSON file (e.g.
                           data/umaps/segments/all.json). The output name is
                           derived from the filename stem (e.g. "all-passages")
                           and the label from the source index (e.g. "All segments
                           (passages)"). Both can be overridden with --name / --label.

Options:
  --name NAME              Override the output identifier (default: <stem>-passages).
  --label LABEL            Override the human-readable label (default: <source label> (passages)).
  --cosine-threshold F     Cosine distance threshold to start a new passage (default: 0.3).
                           Lower = more splits, higher = fewer, larger passages.
  --min-segments N         Minimum segments per passage; shorter passages are merged
                           into the adjacent one (default: 3).
  --max-segments N         Maximum segments per passage; longer passages are force-split
                           at the highest internal distance boundary (default: 20).
  --include-song-type ...  Only include recordings of these song types.
                           Choices: original cover jam unidentified. (default: all)
  --include-origin ...     Only include recordings with these origins.
                           Choices: pretrimmed vad_segment. (default: all)
  --output-dir DIR         Output directory (default: data/umaps/recording-passage).

Examples:
  # All recordings
  python -m pipeline.scripts.build_passage_umap data/umaps/segments/all.json

  # Songs only, tighter threshold (more passages per recording)
  python -m pipeline.scripts.build_passage_umap data/umaps/segments/songs.json \\
    --cosine-threshold 0.2
"""

import argparse
import json
import logging
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

load_dotenv()

from pipeline.db.models import Recording, Segment, Session as DbSession, Song
from pipeline.db.session import SessionLocal

DEFAULT_OUTPUT_DIR = Path("data/umaps/recording-passage")
SONG_TYPE_CHOICES = ["original", "cover", "jam", "unidentified"]
ORIGIN_CHOICES = ["pretrimmed", "vad_segment"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build passage-level UMAP from existing segment UMAP coordinates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "source_umap",
        type=Path,
        help="Path to a segment-level UMAP JSON file (e.g. data/umaps/segments/all.json)",
    )
    p.add_argument("--name", default=None,
                   help="Override output identifier (default: <stem>-passages)")
    p.add_argument("--label", default=None,
                   help="Override human-readable label (default: <source label> (passages))")
    p.add_argument("--cosine-threshold", type=float, default=0.3,
                   help="Cosine distance threshold to split passages (default: 0.3)")
    p.add_argument("--min-segments", type=int, default=3,
                   help="Minimum segments per passage (default: 3)")
    p.add_argument("--max-segments", type=int, default=20,
                   help="Maximum segments per passage (default: 20)")
    p.add_argument(
        "--include-song-type",
        nargs="+",
        choices=SONG_TYPE_CHOICES,
        metavar="TYPE",
        help=f"Song types to include: {SONG_TYPE_CHOICES}. Default: all.",
    )
    p.add_argument(
        "--include-origin",
        nargs="+",
        choices=ORIGIN_CHOICES,
        metavar="ORIGIN",
        help=f"Recording origins to include: {ORIGIN_CHOICES}. Default: all.",
    )
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})")
    return p.parse_args()


def _resolve_source_label(source_path: Path) -> str | None:
    """Look up the human-readable label for a source UMAP from its sibling index.json."""
    index_path = source_path.parent / "index.json"
    if not index_path.exists():
        return None
    try:
        for entry in json.loads(index_path.read_text()):
            if entry.get("name") == source_path.stem:
                return entry.get("label")
    except Exception:
        pass
    return None


def _unpack(raw: bytes) -> np.ndarray:
    return np.frombuffer(raw, dtype="<f4").copy()


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    a_norm = float(np.linalg.norm(a))
    b_norm = float(np.linalg.norm(b))
    if a_norm == 0 or b_norm == 0:
        return 1.0
    return float(1.0 - np.dot(a, b) / (a_norm * b_norm))


def _detect_passages(
    segments: list,
    threshold: float,
    min_segs: int,
    max_segs: int,
) -> list[list]:
    """Group a recording's segments into passages via change-point detection.

    Args:
        segments: List of segment rows sorted by start_seconds, each with
                  a clap_embedding attribute.
        threshold: Cosine distance threshold above which a new passage begins.
        min_segs: Minimum number of segments per passage; short passages are
                  merged into their preceding neighbour.
        max_segs: Maximum number of segments per passage; longer runs are force-
                  split at the highest internal distance boundary.

    Returns:
        List of passage groups, each group being a list of segment rows.
    """
    if not segments:
        return []
    if len(segments) <= min_segs:
        return [segments]

    embeddings = [_unpack(s.clap_embedding) for s in segments]

    groups: list[list] = []
    current: list = [segments[0]]

    for i in range(1, len(segments)):
        dist = _cosine_distance(embeddings[i - 1], embeddings[i])
        force_split = len(current) >= max_segs

        if (dist > threshold or force_split) and len(current) >= min_segs:
            groups.append(current)
            current = [segments[i]]
        else:
            current.append(segments[i])

    groups.append(current)

    # Merge trailing groups that are too short into their predecessor
    merged: list[list] = []
    for g in groups:
        if merged and len(g) < min_segs:
            merged[-1].extend(g)
        else:
            merged.append(g)

    # Force-split any group that still exceeds max_segs due to merging,
    # dividing at the highest internal cosine distance boundary
    final: list[list] = []
    for g in merged:
        if len(g) <= max_segs:
            final.append(g)
            continue
        embs = [_unpack(s.clap_embedding) for s in g]
        dists = [_cosine_distance(embs[j], embs[j + 1]) for j in range(len(embs) - 1)]
        split_idx = int(np.argmax(dists)) + 1
        final.append(g[:split_idx])
        final.append(g[split_idx:])

    return [g for g in final if g]


def _update_index(output_dir: Path, name: str, label: str, count: int, filters: dict) -> None:
    index_path = output_dir / "index.json"
    entries: list[dict] = json.loads(index_path.read_text()) if index_path.exists() else []
    entries = [e for e in entries if e["name"] != name]
    entries.append({"name": name, "label": label, "count": count, "filters": filters})
    entries.sort(key=lambda e: e["name"])
    index_path.write_text(json.dumps(entries, indent=2))
    log.info("Index updated: %s", index_path)


def main() -> None:
    args = _parse_args()

    source_path: Path = args.source_umap
    if not source_path.exists():
        log.error("Source UMAP not found: %s — run build_segment_umap.py first.", source_path)
        return

    name = args.name or f"{source_path.stem}-passages"
    source_label = _resolve_source_label(source_path)
    label = args.label or (f"{source_label} (passages)" if source_label else name)

    log.info("Loading source UMAP coordinates from %s…", source_path)
    source_points = json.loads(source_path.read_text())
    coord_map: dict[int, tuple[float, float]] = {
        p["segment_id"]: (p["x"], p["y"]) for p in source_points
    }
    log.info("Loaded %d segment coordinates", len(coord_map))

    include_song_types: set[str | None] | None = None
    if args.include_song_type:
        include_song_types = {
            None if t == "unidentified" else t for t in args.include_song_type
        }

    include_origins: set[str] | None = None
    if args.include_origin:
        include_origins = set(args.include_origin)

    db = SessionLocal()
    try:
        log.info("Loading segments with CLAP embeddings from DB…")
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
                Recording.origin,
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
    finally:
        db.close()

    if not rows:
        log.error("No segments with CLAP embeddings found — run extract_clap_embeddings.py first.")
        return

    log.info("Loaded %d segments before filtering", len(rows))

    if include_song_types is not None:
        rows = [r for r in rows if r.song_type in include_song_types]
        log.info("After song-type filter (%s): %d segments", args.include_song_type, len(rows))

    if include_origins is not None:
        rows = [r for r in rows if r.origin in include_origins]
        log.info("After origin filter (%s): %d segments", args.include_origin, len(rows))

    if not rows:
        log.error("No segments remain after filtering.")
        return

    by_recording: dict[int, list] = {}
    recording_meta: dict[int, dict] = {}
    for r in rows:
        by_recording.setdefault(r.recording_id, []).append(r)
        if r.recording_id not in recording_meta:
            recording_meta[r.recording_id] = {
                "recording_title": r.recording_title,
                "audio_path": r.audio_path,
                "origin": r.origin,
                "session_date": r.session_date,
                "song_title": r.song_title,
                "song_type": r.song_type,
            }

    log.info(
        "Building passages for %d recordings (threshold=%.2f, min=%d, max=%d)…",
        len(by_recording),
        args.cosine_threshold,
        args.min_segments,
        args.max_segments,
    )

    passages: list[dict] = []
    passage_counts: list[int] = []
    no_coords_count = 0
    passage_id = 0

    for recording_id, segs in by_recording.items():
        segs.sort(key=lambda s: s.start_seconds)
        groups = _detect_passages(segs, args.cosine_threshold, args.min_segments, args.max_segments)
        passage_counts.append(len(groups))
        meta = recording_meta[recording_id]

        for group in groups:
            seg_ids = [s.id for s in group]
            coords = [coord_map[sid] for sid in seg_ids if sid in coord_map]
            if not coords:
                no_coords_count += 1
                continue

            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            rms_vals = [s.mean_rms for s in group if s.mean_rms is not None]
            cent_vals = [s.mean_spectral_centroid for s in group if s.mean_spectral_centroid is not None]

            passages.append({
                "passage_id": passage_id,
                "recording_id": recording_id,
                "x": round(float(np.mean(xs)), 5),
                "y": round(float(np.mean(ys)), 5),
                "start_seconds": group[0].start_seconds,
                "end_seconds": group[-1].end_seconds,
                "duration": round(group[-1].end_seconds - group[0].start_seconds, 2),
                "segment_count": len(group),
                "recording_title": meta["recording_title"],
                "audio_path": meta["audio_path"],
                "origin": meta["origin"],
                "session_date": meta["session_date"],
                "song_title": meta["song_title"],
                "song_type": meta["song_type"],
                "mean_rms": round(float(np.mean(rms_vals)), 6) if rms_vals else None,
                "mean_spectral_centroid": round(float(np.mean(cent_vals)), 2) if cent_vals else None,
            })
            passage_id += 1

    if no_coords_count:
        log.warning(
            "Skipped %d passages because their segments had no coordinates in the source UMAP.",
            no_coords_count,
        )

    counts_arr = np.array(passage_counts)
    log.info(
        "Passage stats per recording — mean: %.1f, median: %.1f, min: %d, max: %d",
        counts_arr.mean(),
        np.median(counts_arr),
        counts_arr.min(),
        counts_arr.max(),
    )
    log.info("Total passages: %d", len(passages))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / f"{name}.json"
    with open(output_path, "w") as f:
        json.dump(passages, f, separators=(",", ":"))

    size_mb = output_path.stat().st_size / 1e6
    log.info("Written %d passages to %s (%.1f MB)", len(passages), output_path, size_mb)

    filters = {
        "source_umap": str(source_path),
        "include_song_type": args.include_song_type,
        "include_origin": args.include_origin,
        "cosine_threshold": args.cosine_threshold,
        "min_segments": args.min_segments,
        "max_segments": args.max_segments,
    }
    _update_index(args.output_dir, name, label, len(passages), filters)


if __name__ == "__main__":
    main()
