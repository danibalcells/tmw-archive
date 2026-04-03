"""Build an intermediate N-dimensional UMAP of CLAP segment embeddings.

Fits UMAP to a higher-dimensional output (default 20D) and saves the reduced
embeddings as flat files for downstream use by clustering and HMM scripts.
Unlike the 2D UMAP used for visualization, this intermediate reduction preserves
enough structure for HDBSCAN to work well without the curse of dimensionality.

Output files (in --output-dir):
  umap_<N>d.npy      — float32 array, shape (num_segments, N)
  umap_<N>d_ids.json — JSON list of segment IDs in the same row order

Usage:
  python -m pipeline.scripts.build_intermediate_umap [options]

Options:
  --n-components N     UMAP output dimensions (default: 20)
  --n-neighbors N      UMAP n_neighbors param (default: 15)
  --min-dist F         UMAP min_dist param (default: 0.1)
  --include-type ...   Only include segments with these effective types.
                       Choices: original cover jam non-musical unreviewed.
                       (default: all types)
  --include-origin ... Only include segments from recordings with these
                       origins. Choices: pretrimmed vad_segment.
                       (default: all origins)
  --output-dir DIR     Directory to write output files (default: data/eternal-rehearsal)

Examples:
  # Default: all segments, 20D output
  python -m pipeline.scripts.build_intermediate_umap

  # Musical content only, 15D
  python -m pipeline.scripts.build_intermediate_umap \\
    --n-components 15 \\
    --include-type original cover jam

  # Pre-trimmed songs, 20D
  python -m pipeline.scripts.build_intermediate_umap \\
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

from pipeline.db.models import Recording, Segment
from pipeline.db.models import Session as DbSession
from pipeline.db.models import Song
from pipeline.db.session import SessionLocal

DEFAULT_OUTPUT_DIR = Path("data/eternal-rehearsal")
EFFECTIVE_TYPE_CHOICES = ["original", "cover", "jam", "non-musical", "unreviewed"]
ORIGIN_CHOICES = ["pretrimmed", "vad_segment"]
NON_MUSICAL_TYPES = {"banter", "tuning", "noodling", "count_in", "silence", "other"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _effective_type(content_type: str | None, song_type: str | None) -> str:
    if content_type == "song_take":
        return song_type or "unreviewed"
    if content_type == "jam":
        return "jam"
    if content_type in NON_MUSICAL_TYPES:
        return "non-musical"
    return "unreviewed"


def _unpack(raw: bytes) -> np.ndarray:
    return np.frombuffer(raw, dtype="<f4").copy()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build intermediate N-dim UMAP of CLAP segment embeddings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--n-components",
        type=int,
        default=20,
        help="UMAP output dimensions (default: 20)",
    )
    p.add_argument(
        "--n-neighbors",
        type=int,
        default=15,
        help="UMAP n_neighbors (default: 15)",
    )
    p.add_argument(
        "--min-dist",
        type=float,
        default=0.1,
        help="UMAP min_dist (default: 0.1)",
    )
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
    return p.parse_args()


def main() -> None:
    args = _parse_args()

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

    log.info(
        "Fitting UMAP (n_components=%d, n_neighbors=%d, min_dist=%.2f, metric=cosine)…",
        args.n_components,
        args.n_neighbors,
        args.min_dist,
    )
    import umap  # noqa: PLC0415

    reducer = umap.UMAP(
        n_components=args.n_components,
        metric="cosine",
        n_neighbors=args.n_neighbors,
        min_dist=args.min_dist,
        random_state=42,
        verbose=True,
    )
    reduced = reducer.fit_transform(matrix).astype(np.float32)
    log.info("UMAP done — reduced shape: %s", reduced.shape)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    stem = f"umap_{args.n_components}d"
    npy_path = args.output_dir / f"{stem}.npy"
    ids_path = args.output_dir / f"{stem}_ids.json"

    np.save(str(npy_path), reduced)
    ids_path.write_text(json.dumps([int(r.id) for r in rows]))

    npy_mb = npy_path.stat().st_size / 1e6
    ids_kb = ids_path.stat().st_size / 1e3
    log.info("Embeddings → %s (%.1f MB)", npy_path, npy_mb)
    log.info("IDs        → %s (%.1f KB)", ids_path, ids_kb)


if __name__ == "__main__":
    main()
