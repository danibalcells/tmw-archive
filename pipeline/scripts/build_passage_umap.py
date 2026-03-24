"""Build passage-level UMAP projections for the Recording Passage Mood Map.

For each recording, groups consecutive segments into acoustically coherent passages
using cosine-distance change-point detection on CLAP embeddings. Each passage is
represented by the mean UMAP coordinates of its constituent segments (read from an
existing segment-level UMAP JSON), plus averaged feature values.

Usage:
  # Single source file
  python -m pipeline.scripts.build_passage_umap SOURCE_UMAP [options]

  # Process every segment UMAP in the segments directory
  python -m pipeline.scripts.build_passage_umap --each [options]

Arguments:
  SOURCE_UMAP              Path to a segment-level UMAP JSON file (e.g.
                           data/umaps/segments/all.json). The output name is
                           derived from the filename stem (e.g. "all-passages")
                           and the label from the source index (e.g. "All segments
                           (passages)"). Both can be overridden with --name / --label.

Options:
  --each                   Process every segment UMAP JSON found in --segments-dir.
                           When used, SOURCE_UMAP must not be given, and --name /
                           --label are ignored (derived per file as usual).
  --segments-dir DIR       Directory to scan when using --each
                           (default: data/umaps/segments).
  --name NAME              Override the output identifier (default: <stem>-passages).
                           Ignored when --each is used.
  --label LABEL            Override the human-readable label (default: <source label> (passages)).
                           Ignored when --each is used.
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
  # Single file
  python -m pipeline.scripts.build_passage_umap data/umaps/segments/all.json

  # Every segment UMAP with a tighter threshold
  python -m pipeline.scripts.build_passage_umap --each --cosine-threshold 0.2
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

DEFAULT_SEGMENTS_DIR = Path("data/umaps/segments")
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
        nargs="?",
        help="Path to a segment-level UMAP JSON file (e.g. data/umaps/segments/all.json). "
             "Omit when using --each.",
    )
    p.add_argument(
        "--each",
        action="store_true",
        help="Process every segment UMAP JSON found in --segments-dir.",
    )
    p.add_argument(
        "--segments-dir",
        type=Path,
        default=DEFAULT_SEGMENTS_DIR,
        help=f"Directory to scan when using --each (default: {DEFAULT_SEGMENTS_DIR})",
    )
    p.add_argument("--name", default=None,
                   help="Override output identifier (default: <stem>-passages). Ignored with --each.")
    p.add_argument("--label", default=None,
                   help="Override human-readable label (default: <source label> (passages)). Ignored with --each.")
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


def _load_rows(db_rows_cache: list | None) -> list:
    """Load all segments with CLAP embeddings from the DB (cached across calls)."""
    if db_rows_cache is not None:
        return db_rows_cache
    db = SessionLocal()
    try:
        return (
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


def _build_one(
    source_path: Path,
    name: str,
    label: str,
    rows: list,
    args: argparse.Namespace,
) -> bool:
    """Build a single passage UMAP from one source file. Returns True on success."""
    log.info("── %s → %s (%s) ──", source_path.name, name, label)

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

    filtered = rows
    if include_song_types is not None:
        filtered = [r for r in filtered if r.song_type in include_song_types]
    if include_origins is not None:
        filtered = [r for r in filtered if r.origin in include_origins]

    if not filtered:
        log.error("No segments remain after filtering — skipping %s.", name)
        return False

    by_recording: dict[int, list] = {}
    recording_meta: dict[int, dict] = {}
    for r in filtered:
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
            coords = [coord_map[s.id] for s in group if s.id in coord_map]
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
    return True


def main() -> None:
    args = _parse_args()

    if args.each and args.source_umap:
        log.error("Provide either a SOURCE_UMAP path or --each, not both.")
        return
    if not args.each and not args.source_umap:
        log.error("Provide a SOURCE_UMAP path, or use --each to process the whole segments directory.")
        return

    if args.each:
        source_files = sorted(
            p for p in args.segments_dir.glob("*.json") if p.stem != "index"
        )
        if not source_files:
            log.error("No segment UMAP JSON files found in %s.", args.segments_dir)
            return
        log.info("Found %d segment UMAPs to process: %s",
                 len(source_files), [p.name for p in source_files])
        if args.name or args.label:
            log.warning("--name and --label are ignored when using --each.")
    else:
        source_files = [args.source_umap]

    # Load DB rows once and reuse across all source files
    log.info("Loading segments with CLAP embeddings from DB…")
    rows = _load_rows(None)
    if not rows:
        log.error("No segments with CLAP embeddings found — run extract_clap_embeddings.py first.")
        return
    log.info("Loaded %d segments", len(rows))

    for source_path in source_files:
        if not source_path.exists():
            log.error("Source UMAP not found: %s — skipping.", source_path)
            continue

        name = args.name or f"{source_path.stem}-passages"
        source_label = _resolve_source_label(source_path)
        label = args.label or (f"{source_label} (passages)" if source_label else name)

        # In --each mode, always derive name/label from the source file
        if args.each:
            name = f"{source_path.stem}-passages"
            label = f"{source_label} (passages)" if source_label else name

        _build_one(source_path, name, label, rows, args)


if __name__ == "__main__":
    main()
