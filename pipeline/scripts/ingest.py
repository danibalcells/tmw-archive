"""Archive ingestion script.

Walks the archive, creates Session/Song/Recording rows, runs FFmpeg
silence detection on raw session recordings, and transcodes all audio
to MP3 320kbps under PROCESSED_ROOT/recordings/{id}.mp3.

Usage:
    python -m pipeline.scripts.ingest [--tier-1] [--overwrite] [--dry-run]

Flags:
    --tier-1    Restrict to the Tier 1 dev subset (see docs/tier1.md).
    --overwrite When a source path already has Recording rows in the DB,
                delete them (cascading to Segments) and re-ingest.
                Default: skip already-ingested paths.
    --dry-run   Log what would happen without writing to DB or disk.
"""

import argparse
import logging

from pipeline.config import ARCHIVE_ROOT, PROCESSED_ROOT
from pipeline.ingest.core import run_ingest
from pipeline.ingest.scanner import scan_archive


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tier-1", action="store_true", help="Ingest Tier 1 dev subset only")
    parser.add_argument("--overwrite", action="store_true", help="Re-ingest and overwrite existing recordings")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB or disk")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug-level logging")
    args = parser.parse_args()

    _setup_logging(args.verbose)
    log = logging.getLogger(__name__)

    log.info("Archive root:   %s", ARCHIVE_ROOT)
    log.info("Processed root: %s", PROCESSED_ROOT)
    if args.dry_run:
        log.info("DRY RUN — no DB writes or transcoding")

    items = scan_archive(ARCHIVE_ROOT, tier1_only=args.tier_1)
    log.info("Scanned %d item(s) to ingest", len(items))

    summary = run_ingest(items, overwrite=args.overwrite, dry_run=args.dry_run)

    log.info(
        "Ingest complete — created: %d  skipped: %d  failed: %d",
        summary["created"],
        summary["skipped"],
        summary["failed"],
    )


if __name__ == "__main__":
    main()
