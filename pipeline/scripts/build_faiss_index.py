"""Build (or rebuild) the FAISS index from all embedded segments in the DB.

Run this after extract_clap_embeddings.py to make the index available to the
API. Safe to run repeatedly — it always rebuilds from the current DB state.

Usage:
  python -m pipeline.scripts.build_faiss_index [--output PATH]

Options:
  --output PATH    Where to write the index (default: data/clap.index)
"""

import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from pipeline.db.session import SessionLocal
from pipeline.features.faiss_index import DEFAULT_INDEX_PATH, build_index, save_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build FAISS index from CLAP embeddings.")
    p.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_INDEX_PATH,
        help=f"Output path for the index (default: {DEFAULT_INDEX_PATH})",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    db = SessionLocal()
    try:
        log.info("Reading embeddings from DB…")
        index = build_index(db)
        n = index.ntotal
        log.info("Index built: %d vectors", n)
        if n == 0:
            log.warning("No embeddings found — run extract_clap_embeddings.py first.")
            return
        save_index(index, args.output)
        log.info("Index saved to %s", args.output)
    finally:
        db.close()


if __name__ == "__main__":
    main()
