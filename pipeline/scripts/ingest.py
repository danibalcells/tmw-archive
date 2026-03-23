"""Archive ingestion script.

Walks the archive, creates Session/Song/Recording rows, runs FFmpeg
silence detection on raw session recordings, and transcodes all audio
to MP3 320kbps under PROCESSED_ROOT/recordings/{id}.mp3.

Usage:
    python -m pipeline.scripts.ingest [--tier-1 | --tier-2] [--overwrite] [--dry-run]
                                      [--ingest-config PATH]

Flags:
    --tier-1          Restrict to the Tier 1 dev subset (see docs/tier1.md).
    --tier-2          Restrict to the Tier 2 dev subset (see docs/tier2.md).
    --overwrite       When a source path already has Recording rows in the DB,
                      delete them (cascading to Segments) and re-ingest.
                      Default: skip already-ingested paths.
    --dry-run         Log what would happen without writing to DB or disk.
    --ingest-config   Path to a YAML config file (default: pipeline/ingest.yaml).
                      Controls VAD thresholds and other ingestion parameters.
"""

import argparse
import logging
from pathlib import Path

from pipeline.config import ARCHIVE_ROOT, PROCESSED_ROOT
from pipeline.ingest.core import run_ingest
from pipeline.ingest.scanner import scan_archive
from pipeline.ingest.vad import load_ingest_config


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    tier_group = parser.add_mutually_exclusive_group()
    tier_group.add_argument("--tier-1", action="store_true", help="Ingest Tier 1 dev subset only (see docs/tier1.md)")
    tier_group.add_argument("--tier-2", action="store_true", help="Ingest Tier 2 dev subset only (see docs/tier2.md)")
    parser.add_argument("--overwrite", action="store_true", help="Re-ingest and overwrite existing recordings")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB or disk")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug-level logging")
    parser.add_argument(
        "--ingest-config",
        metavar="PATH",
        type=Path,
        default=None,
        help="Path to ingest YAML config (default: pipeline/ingest.yaml)",
    )
    args = parser.parse_args()

    _setup_logging(args.verbose)
    log = logging.getLogger(__name__)

    log.info("Archive root:   %s", ARCHIVE_ROOT)
    log.info("Processed root: %s", PROCESSED_ROOT)
    if args.dry_run:
        log.info("DRY RUN — no DB writes or transcoding")

    ingest_config = load_ingest_config(args.ingest_config)
    if args.ingest_config:
        log.info("Ingest config:  %s", args.ingest_config)

    items = scan_archive(ARCHIVE_ROOT, tier1_only=args.tier_1, tier2_only=args.tier_2)
    log.info("Scanned %d item(s) to ingest", len(items))

    summary = run_ingest(items, overwrite=args.overwrite, dry_run=args.dry_run, ingest_config=ingest_config)

    log.info(
        "Ingest complete — created: %d  skipped: %d  failed: %d",
        summary["created"],
        summary["skipped"],
        summary["failed"],
    )


if __name__ == "__main__":
    main()
