from dotenv import load_dotenv

load_dotenv()

import argparse
import logging
from collections import defaultdict

import numpy as np

from pipeline.db.models import Recording
from pipeline.db.session import SessionLocal

SILENCE_THRESHOLD = 0.005
INTERSTITIAL_RMS = 0.03
VARIANCE_THRESHOLD = 0.001
MUSICAL_RMS = 0.04
HIGH_CONFIDENCE_THRESHOLD = 0.70
COUNT_IN_MAX_DURATION = 10.0
TUNING_MAX_DURATION = 120.0
JAM_MIN_DURATION = 180.0
JAM_CONFIDENCE_CEILING = 0.30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Heuristic pre-classification for unlabeled recordings."
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print classifications without writing to the DB",
    )
    p.add_argument(
        "--recording-id",
        type=int,
        default=None,
        help="Classify a single recording by ID",
    )
    return p.parse_args()


def _best_candidate_confidence(rec: Recording) -> float:
    non_rejected = [c for c in rec.song_match_candidates if c.status != "rejected"]
    if not non_rejected:
        return 0.0
    return max(c.confidence for c in non_rejected)


def _segment_stats(
    rec: Recording,
) -> tuple[float | None, float | None, float | None]:
    rms_values = [s.mean_rms for s in rec.segments if s.mean_rms is not None]
    centroid_values = [
        s.mean_spectral_centroid
        for s in rec.segments
        if s.mean_spectral_centroid is not None
    ]

    if not rms_values:
        return None, None, None

    rms_arr = np.array(rms_values, dtype=np.float64)
    mean_rms = float(rms_arr.mean())
    rms_variance = float(rms_arr.var())
    mean_centroid = float(np.mean(centroid_values)) if centroid_values else None

    return mean_rms, rms_variance, mean_centroid


def _classify(
    rec: Recording,
    best_confidence: float,
    mean_rms: float | None,
    rms_variance: float | None,
) -> str | None:
    duration = rec.duration_seconds or 0.0

    if duration < COUNT_IN_MAX_DURATION:
        return "count_in"

    if mean_rms is not None and mean_rms < SILENCE_THRESHOLD:
        return "silence"

    if (
        mean_rms is not None
        and rms_variance is not None
        and duration < TUNING_MAX_DURATION
        and mean_rms < INTERSTITIAL_RMS
        and rms_variance < VARIANCE_THRESHOLD
    ):
        return "tuning"

    if (
        mean_rms is not None
        and duration > JAM_MIN_DURATION
        and mean_rms > MUSICAL_RMS
        and best_confidence < JAM_CONFIDENCE_CEILING
    ):
        return "jam"

    return None


def main() -> None:
    args = _parse_args()
    db = SessionLocal()

    try:
        q = db.query(Recording).filter(
            Recording.song_id.is_(None),
            (Recording.content_type.is_(None))
            | (Recording.content_type_source == "auto"),
        )
        if args.recording_id is not None:
            q = q.filter(Recording.id == args.recording_id)
        candidates = q.all()

        log.info("Recordings to classify: %d", len(candidates))

        counts: dict[str, int] = defaultdict(int)
        skipped_high_conf = 0
        left_null = 0

        for rec in candidates:
            best_conf = _best_candidate_confidence(rec)

            if best_conf > HIGH_CONFIDENCE_THRESHOLD:
                skipped_high_conf += 1
                log.debug(
                    "Recording %d: high-confidence candidate (%.3f) — skipping",
                    rec.id,
                    best_conf,
                )
                continue

            mean_rms, rms_variance, mean_centroid = _segment_stats(rec)
            suggestion = _classify(rec, best_conf, mean_rms, rms_variance)

            if suggestion is None:
                left_null += 1
                if args.dry_run:
                    log.info(
                        "DRY  recording %d → NULL (needs review, dur=%.1fs rms=%s conf=%.3f)",
                        rec.id,
                        rec.duration_seconds or 0,
                        f"{mean_rms:.4f}" if mean_rms is not None else "n/a",
                        best_conf,
                    )
                continue

            counts[suggestion] += 1

            if args.dry_run:
                log.info(
                    "DRY  recording %d → %s (dur=%.1fs rms=%s conf=%.3f)",
                    rec.id,
                    suggestion,
                    rec.duration_seconds or 0,
                    f"{mean_rms:.4f}" if mean_rms is not None else "n/a",
                    best_conf,
                )
            else:
                rec.content_type = suggestion
                rec.content_type_source = "auto"

        if not args.dry_run:
            db.commit()

        log.info(
            "Done. skipped_high_conf=%d left_null=%d%s",
            skipped_high_conf,
            left_null,
            " (dry run)" if args.dry_run else "",
        )
        for ct, count in sorted(counts.items()):
            log.info("  %-12s %d", ct, count)

    finally:
        db.close()


if __name__ == "__main__":
    main()
