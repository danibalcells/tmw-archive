"""Build a UMAP 2D projection of CLAP-embedded segments and write to JSON.

Reads segments from the DB (with optional source filters), fits a UMAP
projection, and writes output to data/umaps/<name>.json. Also maintains
data/umaps/index.json so the API knows what's available.

Each point in the output carries an `effective_type` field with one of five
values derived from the recording's content_type and song_type:
  original    — song_take linked to an original song
  cover       — song_take linked to a cover
  jam         — classified as a jam (with or without a named Song)
  non-musical — banter, tuning, noodling, count_in, silence, or other
  unreviewed  — content_type not yet set

Usage:
  python -m pipeline.scripts.build_segment_umap [options]

Options:
  --name NAME          Identifier for this projection (default: all).
                       Used as the filename and in the frontend dropdown.
  --label LABEL        Human-readable label shown in the UI (default: --name).
  --include-type ...   Only include segments with these effective types.
                       Choices: original cover jam non-musical unreviewed.
                       (default: all types)
  --include-origin ... Only include segments from recordings with these
                       origins. Choices: pretrimmed vad_segment.
                       (default: all origins)
  --output-dir DIR     Directory to write JSON files (default: data/umaps/segments).
  --n-neighbors N      UMAP n_neighbors param (default: 15).
  --min-dist F         UMAP min_dist param (default: 0.1).

Examples:
  # All segments (default)
  python -m pipeline.scripts.build_segment_umap

  # Musical content only
  python -m pipeline.scripts.build_segment_umap \\
    --name musical \\
    --label "Musical only" \\
    --include-type original cover jam

  # Pre-trimmed songs only
  python -m pipeline.scripts.build_segment_umap \\
    --name songs \\
    --label "Songs (pre-trimmed)" \\
    --include-origin pretrimmed \\
    --include-type original cover
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

DEFAULT_OUTPUT_DIR = Path("data/umaps/segments")
EFFECTIVE_TYPE_CHOICES = ["original", "cover", "jam", "non-musical", "unreviewed"]
ORIGIN_CHOICES = ["pretrimmed", "vad_segment"]
NON_MUSICAL_TYPES = {"banter", "tuning", "noodling", "count_in", "silence", "other"}


def _effective_type(content_type: str | None, song_type: str | None) -> str:
    if content_type == "song_take":
        return song_type or "unreviewed"
    if content_type == "jam":
        return "jam"
    if content_type in NON_MUSICAL_TYPES:
        return "non-musical"
    return "unreviewed"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build UMAP projection of CLAP segment embeddings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--name", default="all", help="Identifier for this projection (default: all)")
    p.add_argument("--label", default=None, help="Human-readable label for the UI (default: --name)")
    p.add_argument(
        "--include-type",
        nargs="+",
        choices=EFFECTIVE_TYPE_CHOICES,
        metavar="TYPE",
        help=f"Effective types to include: {EFFECTIVE_TYPE_CHOICES}. Default: all.",
    )
    p.add_argument(
        "--include-origin",
        nargs="+",
        choices=ORIGIN_CHOICES,
        metavar="ORIGIN",
        help=f"Recording origins to include: {ORIGIN_CHOICES}. Default: all.",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    p.add_argument("--n-neighbors", type=int, default=15, help="UMAP n_neighbors (default: 15)")
    p.add_argument("--min-dist", type=float, default=0.1, help="UMAP min_dist (default: 0.1)")
    return p.parse_args()


def _unpack(raw: bytes) -> np.ndarray:
    return np.frombuffer(raw, dtype="<f4").copy()


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
    label = args.label or args.name

    include_types: set[str] | None = set(args.include_type) if args.include_type else None
    include_origins: set[str] | None = set(args.include_origin) if args.include_origin else None

    db = SessionLocal()
    try:
        log.info("Loading segments with CLAP embeddings…")
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
    finally:
        db.close()

    if not rows:
        log.error("No segments with CLAP embeddings found — run extract_clap_embeddings.py first.")
        return

    log.info("Loaded %d segments before filtering", len(rows))

    if include_types is not None:
        rows = [r for r in rows if _effective_type(r.content_type, r.song_type) in include_types]
        log.info("After type filter (%s): %d segments", args.include_type, len(rows))

    if include_origins is not None:
        rows = [r for r in rows if r.origin in include_origins]
        log.info("After origin filter (%s): %d segments", args.include_origin, len(rows))

    if not rows:
        log.error("No segments remain after filtering — adjust your filter flags.")
        return

    matrix = np.stack([_unpack(r.clap_embedding) for r in rows]).astype(np.float32)
    log.info("Embedding matrix: %s", matrix.shape)

    log.info("Fitting UMAP (n_neighbors=%d, min_dist=%.2f)…", args.n_neighbors, args.min_dist)
    import umap

    reducer = umap.UMAP(
        n_components=2,
        metric="cosine",
        n_neighbors=args.n_neighbors,
        min_dist=args.min_dist,
        random_state=42,
        verbose=True,
    )
    coords = reducer.fit_transform(matrix)
    log.info("UMAP done — coords shape: %s", coords.shape)

    points = []
    for row, (x, y) in zip(rows, coords):
        points.append({
            "segment_id": row.id,
            "recording_id": row.recording_id,
            "x": round(float(x), 5),
            "y": round(float(y), 5),
            "start_seconds": row.start_seconds,
            "end_seconds": row.end_seconds,
            "recording_title": row.recording_title,
            "audio_path": row.audio_path,
            "origin": row.origin,
            "session_date": row.session_date,
            "song_title": row.song_title,
            "effective_type": _effective_type(row.content_type, row.song_type),
            "mean_rms": round(float(row.mean_rms), 6) if row.mean_rms is not None else None,
            "mean_spectral_centroid": round(float(row.mean_spectral_centroid), 2) if row.mean_spectral_centroid is not None else None,
        })

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / f"{args.name}.json"
    with open(output_path, "w") as f:
        json.dump(points, f, separators=(",", ":"))

    size_mb = output_path.stat().st_size / 1e6
    log.info("Written %d points to %s (%.1f MB)", len(points), output_path, size_mb)

    filters = {
        "include_type": args.include_type,
        "include_origin": args.include_origin,
    }
    _update_index(args.output_dir, args.name, label, len(points), filters)


if __name__ == "__main__":
    main()
