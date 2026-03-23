"""Archive ingestion script.

Walks the archive, creates Session/Song/Recording rows, runs FFmpeg
silence detection on raw session recordings, and transcodes all audio
to MP3 320kbps under PROCESSED_ROOT/recordings/{id}.mp3.

Usage:
    python -m pipeline.scripts.ingest [--tier N [N ...]] [--overwrite] [--dry-run]
                                      [--workers N] [--ingest-config PATH]

Flags:
    --tier N [N ...]  Restrict to one or more dev subsets: 1, 2, or both.
                      E.g. --tier 1, --tier 2, --tier 1 2.
    --overwrite       When a source path already has Recording rows in the DB,
                      delete them (cascading to Segments) and re-ingest.
                      Default: skip already-ingested paths.
    --dry-run         Log what would happen without writing to DB or disk.
    --workers / -j    Number of parallel FFmpeg worker processes (default: cpu_count - 1).
                      Use --workers 1 to run sequentially.
    --ingest-config   Path to a YAML config file (default: pipeline/ingest.yaml).
                      Controls VAD thresholds and other ingestion parameters.
"""

import argparse
import logging
import os
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
    parser.add_argument(
        "--tier",
        type=int,
        nargs="+",
        choices=[1, 2],
        metavar="N",
        default=None,
        help="Restrict to dev subset(s): --tier 1, --tier 2, or --tier 1 2",
    )
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
    parser.add_argument(
        "--workers",
        "-j",
        type=int,
        default=max(1, (os.cpu_count() or 2) - 1),
        help="Parallel worker processes for FFmpeg (default: cpu_count - 1). Use 1 for sequential.",
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

    items = scan_archive(ARCHIVE_ROOT, tiers=args.tier)
    log.info("Scanned %d item(s) to ingest", len(items))

    if not args.dry_run:
        log.info("Workers:        %d", args.workers)
    summary = run_ingest(
        items,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        ingest_config=ingest_config,
        workers=args.workers,
    )

    log.info(
        "Ingest complete — created: %d  skipped: %d  failed: %d",
        summary["created"],
        summary["skipped"],
        summary["failed"],
    )


if __name__ == "__main__":
    main()
