from dotenv import load_dotenv

load_dotenv()

import argparse
import logging
import struct
from datetime import datetime, timezone

import numpy as np

try:
    from pipeline.features.coverhunter import EMBEDDING_DIM, unpack_embedding
except ImportError:
    EMBEDDING_DIM = 128

    def unpack_embedding(raw: bytes) -> np.ndarray:
        n = len(raw) // 4
        return np.array(struct.unpack(f"<{n}f", raw), dtype=np.float32)


from pipeline.db.models import Recording, SongMatchCandidate
from pipeline.db.session import SessionLocal

TOP_N_DEFAULT = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def run_matching(top_n: int = TOP_N_DEFAULT, recording_id: int | None = None, dry_run: bool = False) -> None:
    """Run the full song-matching pipeline against the current reference pool.

    Creates its own DB session so it can be called from background tasks or
    scripts without sharing a request-scoped session.
    """
    db = SessionLocal()
    try:
        _run_matching_inner(db, top_n=top_n, recording_id=recording_id, dry_run=dry_run)
    finally:
        db.close()


def _run_matching_inner(
    db,
    top_n: int = TOP_N_DEFAULT,
    recording_id: int | None = None,
    dry_run: bool = False,
) -> None:
    labeled_recs = (
        db.query(Recording)
        .filter(
            Recording.song_id.isnot(None),
            Recording.coverhunter_embedding.isnot(None),
        )
        .all()
    )

    if not labeled_recs:
        log.warning(
            "No labeled recordings with CoverHunter embeddings — nothing to match against."
        )
        return

    labeled_matrix = np.array(
        [unpack_embedding(r.coverhunter_embedding) for r in labeled_recs],
        dtype=np.float32,
    )
    labeled_matrix = _normalize_rows(labeled_matrix)
    labeled_song_ids = [r.song_id for r in labeled_recs]
    labeled_rec_ids = [r.id for r in labeled_recs]

    log.info("Loaded %d labeled reference recordings.", len(labeled_recs))

    unlabeled_q = db.query(Recording).filter(
        Recording.song_id.is_(None),
        Recording.coverhunter_embedding.isnot(None),
    )
    if recording_id is not None:
        unlabeled_q = unlabeled_q.filter(Recording.id == recording_id)
    unlabeled_recs = unlabeled_q.all()

    if not unlabeled_recs:
        log.info("No unlabeled recordings with CoverHunter embeddings found.")
        return

    to_process = []
    for rec in unlabeled_recs:
        existing_statuses = {c.status for c in rec.song_match_candidates}
        if existing_statuses & {"accepted", "rejected"}:
            log.debug(
                "Recording %d has accepted/rejected candidates — skipping", rec.id
            )
            continue
        to_process.append(rec)

    log.info("Unlabeled recordings to process: %d", len(to_process))

    if not to_process:
        log.info("Nothing to do.")
        return

    unlabeled_matrix = np.array(
        [unpack_embedding(r.coverhunter_embedding) for r in to_process],
        dtype=np.float32,
    )
    unlabeled_matrix = _normalize_rows(unlabeled_matrix)

    sim_matrix = unlabeled_matrix @ labeled_matrix.T

    total_candidates_inserted = 0
    top1_confidences: list[float] = []

    for i, rec in enumerate(to_process):
        sims: np.ndarray = sim_matrix[i]

        song_best: dict[int, tuple[float, int]] = {}
        for j in range(len(labeled_recs)):
            song_id = labeled_song_ids[j]
            ref_rec_id = labeled_rec_ids[j]
            sim = float(sims[j])
            if song_id not in song_best or sim > song_best[song_id][0]:
                song_best[song_id] = (sim, ref_rec_id)

        ranked = sorted(
            song_best.items(), key=lambda kv: kv[1][0], reverse=True
        )
        top = ranked[:top_n]

        if not top:
            log.warning("Recording %d: no candidates produced — skipping", rec.id)
            continue

        best_song_id, (best_conf, _) = top[0]

        if dry_run:
            log.info(
                "DRY  recording %d: top match song_id=%d confidence=%.3f",
                rec.id,
                best_song_id,
                best_conf,
            )
            top1_confidences.append(best_conf)
            continue

        for existing in list(rec.song_match_candidates):
            if existing.status == "pending":
                db.delete(existing)
        db.flush()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for rank_idx, (song_id, (confidence, nearest_rec_id)) in enumerate(
            top, start=1
        ):
            db.add(
                SongMatchCandidate(
                    recording_id=rec.id,
                    song_id=song_id,
                    nearest_recording_id=nearest_rec_id,
                    confidence=confidence,
                    rank=rank_idx,
                    status="pending",
                    created_at=now,
                )
            )
            total_candidates_inserted += 1

        db.commit()
        top1_confidences.append(best_conf)
        log.info(
            "DONE recording %d: inserted %d candidates, top-1 confidence=%.3f",
            rec.id,
            len(top),
            best_conf,
        )

    if top1_confidences:
        arr = np.array(top1_confidences)
        log.info(
            "Stats — processed=%d candidates_inserted=%d%s "
            "top1_conf: min=%.3f p25=%.3f median=%.3f p75=%.3f max=%.3f",
            len(to_process),
            total_candidates_inserted,
            " (dry run)" if dry_run else "",
            arr.min(),
            float(np.percentile(arr, 25)),
            float(np.median(arr)),
            float(np.percentile(arr, 75)),
            arr.max(),
        )
    else:
        log.info(
            "Stats — processed=%d candidates_inserted=0%s",
            len(to_process),
            " (dry run)" if dry_run else "",
        )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Match unlabeled recordings to songs via CoverHunter cosine similarity."
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be inserted without writing to the DB",
    )
    p.add_argument(
        "--top-n",
        type=int,
        default=TOP_N_DEFAULT,
        dest="top_n",
        help=f"Number of candidates per recording (default: {TOP_N_DEFAULT})",
    )
    p.add_argument(
        "--recording-id",
        type=int,
        default=None,
        help="Process a single unlabeled recording by ID",
    )
    return p.parse_args()


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return matrix / norms


def main() -> None:
    args = _parse_args()
    run_matching(top_n=args.top_n, recording_id=args.recording_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
