"""Acoustic passage clustering from per-recording SSM passage runs.

Clustering approaches:

  trajectory — PCA-reduced CLAP trajectory at 8 temporal samples (160d) +
               intra-passage SSM structural features (4d) +
               z-scored scalar features (6d) → K-Means.

  spectral   — Full cross-similarity affinity matrix between all passage pairs
               → spectral clustering.

  mert       — Same trajectory approach but using MERT embeddings (1024d)
               instead of CLAP.

  handcrafted — Interpretable acoustic features (MFCCs, spectral stats,
                onset density, chroma entropy, etc.) → K-Means or HDBSCAN.

  combined   — PCA-whitened MERT + handcrafted features → K-Means or HDBSCAN.

Usage:
  python -m pipeline.scripts.cluster_passages --source ssm-k8-t0p5
  python -m pipeline.scripts.cluster_passages --source ssm-k8-t0p5 --method handcrafted --k 20
  python -m pipeline.scripts.cluster_passages --source ssm-k8-t0p5 --method combined --algorithm hdbscan
  python -m pipeline.scripts.cluster_passages --source ssm-k8-t0p5 --method mert,handcrafted --k 15,20,25
  python -m pipeline.scripts.cluster_passages --source ssm-k8-t0p5 --explore
"""

import argparse
import json
import logging
import os
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from scipy.signal import find_peaks
from hdbscan import HDBSCAN
from sklearn.cluster import KMeans, SpectralClustering
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

load_dotenv()

from pipeline.db.models import Segment
from pipeline.db.session import SessionLocal
from pipeline.scripts.explore_clusters import block_until_interrupt, start_servers

DEFAULT_OUTPUT_DIR = Path("data/eternal-rehearsal")
DEFAULT_K = [20]
DEFAULT_N_SAMPLES = 8
DEFAULT_PORT = 8765
TRAJECTORY_N_POINTS = 8
TRAJECTORY_PCA_DIM = 20

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB loading
# ---------------------------------------------------------------------------

def _unpack(raw: bytes) -> np.ndarray:
    return np.frombuffer(raw, dtype="<f4").copy()


def _safe_float(val) -> float | None:
    return float(val) if val is not None else None


def _load_segments_for_ids(db, segment_ids: set[int]) -> dict[int, dict]:
    rows = (
        db.query(Segment)
        .filter(Segment.id.in_(segment_ids))
        .filter(Segment.clap_embedding.isnot(None))
        .all()
    )
    result: dict[int, dict] = {}
    for r in rows:
        d: dict = {
            "embedding": _unpack(r.clap_embedding),
            "mean_rms": _safe_float(r.mean_rms),
            "mean_spectral_centroid": _safe_float(r.mean_spectral_centroid),
            "var_rms": _safe_float(r.var_rms),
            "var_spectral_centroid": _safe_float(r.var_spectral_centroid),
            "mean_spectral_bandwidth": _safe_float(r.mean_spectral_bandwidth),
            "var_spectral_bandwidth": _safe_float(r.var_spectral_bandwidth),
            "mean_spectral_flatness": _safe_float(r.mean_spectral_flatness),
            "var_spectral_flatness": _safe_float(r.var_spectral_flatness),
            "mean_spectral_rolloff": _safe_float(r.mean_spectral_rolloff),
            "var_spectral_rolloff": _safe_float(r.var_spectral_rolloff),
            "mean_zcr": _safe_float(r.mean_zcr),
            "var_zcr": _safe_float(r.var_zcr),
            "onset_density": _safe_float(r.onset_density),
        }
        if r.mert_embedding is not None:
            d["mert_embedding"] = _unpack(r.mert_embedding)
        if r.mean_mfcc is not None:
            d["mean_mfcc"] = _unpack(r.mean_mfcc)
        if r.var_mfcc is not None:
            d["var_mfcc"] = _unpack(r.var_mfcc)
        if r.mean_chroma is not None:
            d["mean_chroma"] = _unpack(r.mean_chroma)
        if r.var_chroma is not None:
            d["var_chroma"] = _unpack(r.var_chroma)
        if r.mean_spectral_contrast is not None:
            d["mean_spectral_contrast"] = _unpack(r.mean_spectral_contrast)
        if r.var_spectral_contrast is not None:
            d["var_spectral_contrast"] = _unpack(r.var_spectral_contrast)
        result[int(r.id)] = d
    return result


# ---------------------------------------------------------------------------
# Shared: boundary features + scalar features
# ---------------------------------------------------------------------------

def _linear_slope(values: list[float | None]) -> float:
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return 0.0
    x = np.arange(len(vals), dtype=np.float32)
    y = np.array(vals, dtype=np.float32)
    return float(np.polyfit(x, y, 1)[0])


def _build_boundary_features(
    passages: list[dict],
    seg_by_id: dict[int, dict],
) -> list[dict]:
    boundary_features: list[dict] = []
    for passage in passages:
        segs = [seg_by_id[sid] for sid in passage["segment_ids"] if sid in seg_by_id]
        entry_seg = segs[0] if segs else None
        exit_seg = segs[-1] if segs else None
        boundary_features.append({
            "passage_id": passage["passage_id"],
            "entry_embedding": entry_seg["embedding"].tolist() if entry_seg else None,
            "exit_embedding": exit_seg["embedding"].tolist() if exit_seg else None,
            "entry_rms": entry_seg["mean_rms"] if entry_seg else None,
            "exit_rms": exit_seg["mean_rms"] if exit_seg else None,
            "entry_spectral_centroid": (
                entry_seg["mean_spectral_centroid"] if entry_seg else None
            ),
            "exit_spectral_centroid": (
                exit_seg["mean_spectral_centroid"] if exit_seg else None
            ),
        })
    return boundary_features


def _get_passage_segment_embeddings(
    passage: dict, seg_by_id: dict[int, dict]
) -> list[np.ndarray]:
    return [
        seg_by_id[sid]["embedding"]
        for sid in passage["segment_ids"]
        if sid in seg_by_id
    ]


# ---------------------------------------------------------------------------
# Method A: trajectory + SSM features → K-Means
# ---------------------------------------------------------------------------

def _resample_trajectory(
    embeddings: list[np.ndarray],
    n_points: int,
    pca: PCA,
) -> np.ndarray:
    if not embeddings:
        return np.zeros(n_points * pca.n_components_, dtype=np.float32)
    mat = np.stack(embeddings)
    mat_pca = pca.transform(mat)
    n_seg = len(embeddings)
    if n_seg == 1:
        return np.tile(mat_pca[0], n_points).astype(np.float32)
    indices = np.linspace(0, n_seg - 1, n_points)
    resampled = np.zeros((n_points, mat_pca.shape[1]), dtype=np.float32)
    for i, idx in enumerate(indices):
        lo = int(np.floor(idx))
        hi = min(lo + 1, n_seg - 1)
        frac = idx - lo
        resampled[i] = (1 - frac) * mat_pca[lo] + frac * mat_pca[hi]
    return resampled.flatten()


def _l2_normalize_rows(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return mat / norms


def _ssm_features(embeddings: list[np.ndarray]) -> np.ndarray:
    n = len(embeddings)
    if n < 2:
        return np.zeros(4, dtype=np.float32)
    mat = _l2_normalize_rows(np.stack(embeddings))
    ssm = (mat @ mat.T).clip(-1.0, 1.0)

    mask = ~np.eye(n, dtype=bool)
    mean_self_sim = float(ssm[mask].mean())

    near_diag_mask = np.zeros((n, n), dtype=bool)
    for d in range(-2, 3):
        if d == 0:
            continue
        near_diag_mask |= np.eye(n, k=d, dtype=bool)
    diag_mean = float(ssm[near_diag_mask].mean()) if near_diag_mask.any() else 0.0
    off_mean = float(ssm[mask & ~near_diag_mask].mean()) if (mask & ~near_diag_mask).any() else 0.0
    diagonal_dominance = diag_mean - off_mean

    far_mask = np.zeros((n, n), dtype=bool)
    for i in range(n):
        for j in range(n):
            if abs(i - j) > 3:
                far_mask[i, j] = True
    repetition_score = float(ssm[far_mask].max()) if far_mask.any() else 0.0

    kernel_size = min(4, n)
    if kernel_size >= 2 and n >= kernel_size:
        half = kernel_size // 2
        kernel = np.ones((kernel_size, kernel_size), dtype=np.float32)
        kernel[:half, half:] = -1.0
        kernel[half:, :half] = -1.0
        novelty = np.zeros(n, dtype=np.float32)
        for i in range(n):
            r0, r1 = max(0, i - half), min(n, i + half)
            c0, c1 = max(0, i - half), min(n, i + half)
            block = ssm[r0:r1, c0:c1]
            kr0, kc0 = r0 - (i - half), c0 - (i - half)
            novelty[i] = float(np.sum(block * kernel[kr0:kr0 + r1 - r0, kc0:kc0 + c1 - c0]))
        if novelty.std() > 0:
            height = float(novelty.mean() + 0.5 * novelty.std())
            peaks, _ = find_peaks(novelty, height=height, distance=2)
            block_count = float(len(peaks))
        else:
            block_count = 0.0
    else:
        block_count = 0.0

    return np.array([mean_self_sim, diagonal_dominance, repetition_score, block_count],
                    dtype=np.float32)


def _build_trajectory_features(
    passages: list[dict],
    seg_by_id: dict[int, dict],
) -> np.ndarray:
    all_embs = []
    for p in passages:
        all_embs.extend(_get_passage_segment_embeddings(p, seg_by_id))
    log.info("Fitting PCA (%dd) on %d segment embeddings…", TRAJECTORY_PCA_DIM, len(all_embs))
    pca = PCA(n_components=TRAJECTORY_PCA_DIM, random_state=42)
    pca.fit(np.stack(all_embs))
    explained = sum(pca.explained_variance_ratio_) * 100
    log.info("PCA explains %.1f%% of CLAP variance in %dd", explained, TRAJECTORY_PCA_DIM)

    n = len(passages)
    traj_dim = TRAJECTORY_N_POINTS * TRAJECTORY_PCA_DIM
    ssm_dim = 4
    scalar_dim = 6
    total_dim = traj_dim + ssm_dim + scalar_dim
    features = np.zeros((n, total_dim), dtype=np.float32)

    for i, passage in enumerate(passages):
        embs = _get_passage_segment_embeddings(passage, seg_by_id)
        features[i, :traj_dim] = _resample_trajectory(embs, TRAJECTORY_N_POINTS, pca)
        features[i, traj_dim:traj_dim + ssm_dim] = _ssm_features(embs)
        segs = [seg_by_id[sid] for sid in passage["segment_ids"] if sid in seg_by_id]
        duration = passage.get("duration") or 0.0
        features[i, traj_dim + ssm_dim + 0] = passage.get("mean_rms") or 0.0
        features[i, traj_dim + ssm_dim + 1] = passage.get("mean_spectral_centroid") or 0.0
        features[i, traj_dim + ssm_dim + 2] = float(np.log1p(duration))
        clap_var = 0.0
        if len(embs) >= 2:
            clap_var = float(np.mean(np.std(np.stack(embs), axis=0)))
        features[i, traj_dim + ssm_dim + 3] = clap_var
        features[i, traj_dim + ssm_dim + 4] = _linear_slope(
            [s["mean_rms"] for s in segs]
        )
        features[i, traj_dim + ssm_dim + 5] = _linear_slope(
            [s["mean_spectral_centroid"] for s in segs]
        )

    scaler = StandardScaler()
    features[:, traj_dim:] = scaler.fit_transform(features[:, traj_dim:])

    log.info(
        "Trajectory features: %dd (trajectory %dd + SSM %dd + scalars %dd)",
        total_dim, traj_dim, ssm_dim, scalar_dim,
    )
    norms_traj = np.linalg.norm(features[:, :traj_dim], axis=1)
    norms_ssm = np.linalg.norm(features[:, traj_dim:traj_dim + ssm_dim], axis=1)
    norms_scalar = np.linalg.norm(features[:, traj_dim + ssm_dim:], axis=1)
    log.info(
        "Feature group norms — trajectory: %.2f, SSM: %.2f, scalars: %.2f",
        norms_traj.mean(), norms_ssm.mean(), norms_scalar.mean(),
    )
    return features


# ---------------------------------------------------------------------------
# Method B: cross-similarity → spectral clustering
# ---------------------------------------------------------------------------

def _build_affinity_matrix(
    passages: list[dict],
    seg_by_id: dict[int, dict],
) -> np.ndarray:
    n = len(passages)
    all_embs_list: list[np.ndarray] = []
    passage_starts: list[int] = []
    passage_lengths: list[int] = []

    for p in passages:
        embs = _get_passage_segment_embeddings(p, seg_by_id)
        if not embs:
            embs = [np.zeros(512, dtype=np.float32)]
        passage_starts.append(len(all_embs_list))
        passage_lengths.append(len(embs))
        all_embs_list.extend(embs)

    E_all = _l2_normalize_rows(np.stack(all_embs_list).astype(np.float32))
    starts = np.array(passage_starts, dtype=np.int64)
    n_total = E_all.shape[0]
    log.info(
        "Building affinity matrix: %d passages, %d total segments…", n, n_total
    )

    affinity = np.zeros((n, n), dtype=np.float32)
    log_interval = max(1, n // 20)

    for i in range(n):
        s_i, l_i = starts[i], passage_lengths[i]
        E_i = E_all[s_i : s_i + l_i]
        cross = E_i @ E_all.T

        for j in range(n):
            s_j, l_j = starts[j], passage_lengths[j]
            block = cross[:, s_j : s_j + l_j]
            affinity[i, j] = block.max(axis=1).mean()

        if (i + 1) % log_interval == 0:
            log.info("  affinity row %d/%d (%.0f%%)", i + 1, n, (i + 1) / n * 100)

    affinity_sym = (affinity + affinity.T) / 2.0
    np.fill_diagonal(affinity_sym, 1.0)

    log.info(
        "Affinity stats — mean: %.4f, std: %.4f, min: %.4f, max: %.4f",
        affinity_sym.mean(),
        affinity_sym.std(),
        affinity_sym[~np.eye(n, dtype=bool)].min(),
        affinity_sym[~np.eye(n, dtype=bool)].max(),
    )
    return affinity_sym


# ---------------------------------------------------------------------------
# Method C: MERT trajectory → K-Means (same arch as trajectory, different emb)
# ---------------------------------------------------------------------------

def _get_passage_segment_mert_embeddings(
    passage: dict, seg_by_id: dict[int, dict]
) -> list[np.ndarray]:
    return [
        seg_by_id[sid]["mert_embedding"]
        for sid in passage["segment_ids"]
        if sid in seg_by_id and "mert_embedding" in seg_by_id[sid]
    ]


MERT_PCA_DIM = 20


def _build_mert_trajectory_features(
    passages: list[dict],
    seg_by_id: dict[int, dict],
) -> np.ndarray:
    all_embs = []
    for p in passages:
        all_embs.extend(_get_passage_segment_mert_embeddings(p, seg_by_id))
    if not all_embs:
        log.error("No MERT embeddings found — run extract_mert_embeddings first")
        raise SystemExit(1)
    log.info("Fitting PCA (%dd) on %d MERT segment embeddings…", MERT_PCA_DIM, len(all_embs))
    pca = PCA(n_components=MERT_PCA_DIM, random_state=42)
    pca.fit(np.stack(all_embs))
    explained = sum(pca.explained_variance_ratio_) * 100
    log.info("PCA explains %.1f%% of MERT variance in %dd", explained, MERT_PCA_DIM)

    n = len(passages)
    traj_dim = TRAJECTORY_N_POINTS * MERT_PCA_DIM
    ssm_dim = 4
    scalar_dim = 6
    total_dim = traj_dim + ssm_dim + scalar_dim
    features = np.zeros((n, total_dim), dtype=np.float32)

    for i, passage in enumerate(passages):
        embs = _get_passage_segment_mert_embeddings(passage, seg_by_id)
        features[i, :traj_dim] = _resample_trajectory(embs, TRAJECTORY_N_POINTS, pca)
        features[i, traj_dim:traj_dim + ssm_dim] = _ssm_features(embs)
        segs = [seg_by_id[sid] for sid in passage["segment_ids"] if sid in seg_by_id]
        duration = passage.get("duration") or 0.0
        features[i, traj_dim + ssm_dim + 0] = passage.get("mean_rms") or 0.0
        features[i, traj_dim + ssm_dim + 1] = passage.get("mean_spectral_centroid") or 0.0
        features[i, traj_dim + ssm_dim + 2] = float(np.log1p(duration))
        emb_var = 0.0
        if len(embs) >= 2:
            emb_var = float(np.mean(np.std(np.stack(embs), axis=0)))
        features[i, traj_dim + ssm_dim + 3] = emb_var
        features[i, traj_dim + ssm_dim + 4] = _linear_slope(
            [s["mean_rms"] for s in segs]
        )
        features[i, traj_dim + ssm_dim + 5] = _linear_slope(
            [s["mean_spectral_centroid"] for s in segs]
        )

    scaler = StandardScaler()
    features[:, traj_dim:] = scaler.fit_transform(features[:, traj_dim:])

    log.info(
        "MERT trajectory features: %dd (traj %dd + SSM %dd + scalars %dd)",
        total_dim, traj_dim, ssm_dim, scalar_dim,
    )
    return features


# ---------------------------------------------------------------------------
# Method D: handcrafted acoustic features
# ---------------------------------------------------------------------------

def _chroma_entropy(chroma_vec: np.ndarray) -> float:
    p = chroma_vec / (chroma_vec.sum() + 1e-10)
    p = p[p > 0]
    return float(-np.sum(p * np.log(p + 1e-10)))


def _chroma_concentration(chroma_vec: np.ndarray) -> float:
    mean_val = chroma_vec.mean()
    return float(chroma_vec.max() / (mean_val + 1e-10))


def _build_handcrafted_features(
    passages: list[dict],
    seg_by_id: dict[int, dict],
) -> np.ndarray:
    feature_names: list[str] = []
    n = len(passages)

    rows: list[np.ndarray] = []
    for passage in passages:
        segs = [seg_by_id[sid] for sid in passage["segment_ids"] if sid in seg_by_id]
        if not segs:
            rows.append(np.zeros(0))
            continue

        feats: list[float] = []

        mfcc_list = [s["mean_mfcc"] for s in segs if "mean_mfcc" in s]
        if mfcc_list:
            mfcc_stack = np.stack(mfcc_list)
            feats.extend(mfcc_stack.mean(axis=0).tolist())
        else:
            feats.extend([0.0] * 13)

        rms_vals = [s["mean_rms"] for s in segs if s["mean_rms"] is not None]
        feats.append(float(np.mean(rms_vals)) if rms_vals else 0.0)
        feats.append(float(np.std(rms_vals)) if len(rms_vals) >= 2 else 0.0)
        feats.append(_linear_slope(rms_vals))

        sc_vals = [s["mean_spectral_centroid"] for s in segs if s["mean_spectral_centroid"] is not None]
        feats.append(float(np.mean(sc_vals)) if sc_vals else 0.0)
        feats.append(float(np.std(sc_vals)) if len(sc_vals) >= 2 else 0.0)
        feats.append(_linear_slope(sc_vals))

        for key in ["mean_spectral_bandwidth", "mean_spectral_flatness",
                     "mean_spectral_rolloff", "mean_zcr"]:
            vals = [s[key] for s in segs if s.get(key) is not None]
            feats.append(float(np.mean(vals)) if vals else 0.0)
            var_key = key.replace("mean_", "var_")
            var_vals = [s[var_key] for s in segs if s.get(var_key) is not None]
            feats.append(float(np.mean(var_vals)) if var_vals else 0.0)

        od_vals = [s["onset_density"] for s in segs if s.get("onset_density") is not None]
        feats.append(float(np.mean(od_vals)) if od_vals else 0.0)

        sc_list = [s["mean_spectral_contrast"] for s in segs if "mean_spectral_contrast" in s]
        if sc_list:
            sc_stack = np.stack(sc_list)
            feats.extend(sc_stack.mean(axis=0).tolist())
        else:
            feats.extend([0.0] * 7)

        chroma_list = [s["mean_chroma"] for s in segs if "mean_chroma" in s]
        if chroma_list:
            chroma_mean = np.stack(chroma_list).mean(axis=0)
            feats.append(_chroma_entropy(chroma_mean))
            feats.append(_chroma_concentration(chroma_mean))
        else:
            feats.extend([0.0, 0.0])

        duration = passage.get("duration") or 0.0
        feats.append(float(np.log1p(duration)))

        rows.append(np.array(feats, dtype=np.float32))

    dim = len(rows[0]) if rows and len(rows[0]) > 0 else 0
    for i, r in enumerate(rows):
        if len(r) != dim:
            rows[i] = np.zeros(dim, dtype=np.float32)

    features = np.stack(rows)
    scaler = StandardScaler()
    features = scaler.fit_transform(features)

    log.info("Handcrafted features: %dd (%d passages)", dim, n)
    return features.astype(np.float32)


# ---------------------------------------------------------------------------
# Method E: combined MERT + handcrafted
# ---------------------------------------------------------------------------

COMBINED_MERT_PCA_DIM = 15


def _build_combined_features(
    passages: list[dict],
    seg_by_id: dict[int, dict],
) -> np.ndarray:
    all_embs = []
    for p in passages:
        all_embs.extend(_get_passage_segment_mert_embeddings(p, seg_by_id))
    if not all_embs:
        log.error("No MERT embeddings found for combined method")
        raise SystemExit(1)

    pca = PCA(n_components=COMBINED_MERT_PCA_DIM, whiten=True, random_state=42)
    pca.fit(np.stack(all_embs))
    explained = sum(pca.explained_variance_ratio_) * 100
    log.info("PCA-whitened MERT: %.1f%% variance in %dd", explained, COMBINED_MERT_PCA_DIM)

    n = len(passages)
    mert_feats = np.zeros((n, COMBINED_MERT_PCA_DIM), dtype=np.float32)
    for i, passage in enumerate(passages):
        embs = _get_passage_segment_mert_embeddings(passage, seg_by_id)
        if embs:
            mat_pca = pca.transform(np.stack(embs))
            mert_feats[i] = mat_pca.mean(axis=0)

    mert_scaler = StandardScaler()
    mert_feats = mert_scaler.fit_transform(mert_feats)

    handcrafted = _build_handcrafted_features(passages, seg_by_id)

    combined = np.hstack([mert_feats, handcrafted])
    log.info(
        "Combined features: %dd (MERT %dd + handcrafted %dd)",
        combined.shape[1], COMBINED_MERT_PCA_DIM, handcrafted.shape[1],
    )
    return combined.astype(np.float32)


# ---------------------------------------------------------------------------
# Clustering drivers
# ---------------------------------------------------------------------------

def _cluster_trajectory(features: np.ndarray, k: int) -> np.ndarray:
    km = KMeans(n_clusters=k, random_state=42, n_init=10)
    return km.fit_predict(features).astype(np.int32)


def _cluster_spectral(affinity: np.ndarray, k: int) -> np.ndarray:
    sc = SpectralClustering(
        n_clusters=k,
        affinity="precomputed",
        random_state=42,
        n_init=10,
        assign_labels="kmeans",
    )
    return sc.fit_predict(affinity).astype(np.int32)


def _cluster_hdbscan(features: np.ndarray, min_cluster_size: int = 15) -> np.ndarray:
    hdb = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=5)
    labels = hdb.fit_predict(features).astype(np.int32)
    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = int(np.sum(labels == -1))
    log.info("HDBSCAN found %d clusters, %d noise points", n_clusters, n_noise)
    return labels


# ---------------------------------------------------------------------------
# Passage types summary (API + UI compatible)
# ---------------------------------------------------------------------------

def _build_passage_types(passages: list[dict]) -> dict:
    by_type: dict[int, list[dict]] = defaultdict(list)
    for p in passages:
        by_type[p["passage_type"]].append(p)

    types: dict[str, dict] = {}
    for type_id in sorted(by_type):
        group = by_type[type_id]
        rms_vals = [p["mean_rms"] for p in group if p.get("mean_rms") is not None]
        cent_vals = [
            p["mean_spectral_centroid"]
            for p in group
            if p.get("mean_spectral_centroid") is not None
        ]
        durations = [p["duration"] for p in group]
        recordings = {p["recording_id"] for p in group}

        song_counts: dict[str, int] = {}
        for p in group:
            if p.get("song_title"):
                song_counts[p["song_title"]] = song_counts.get(p["song_title"], 0) + 1
        top_songs = sorted(song_counts.items(), key=lambda x: -x[1])[:5]

        etype_counts: dict[str, int] = {}
        for p in group:
            et = p.get("effective_type", "unreviewed")
            etype_counts[et] = etype_counts.get(et, 0) + 1

        types[str(type_id)] = {
            "type_id": type_id,
            "count": len(group),
            "n_recordings": len(recordings),
            "mean_duration": round(float(np.mean(durations)), 1),
            "mean_rms": round(float(np.mean(rms_vals)), 6) if rms_vals else None,
            "mean_spectral_centroid": (
                round(float(np.mean(cent_vals)), 2) if cent_vals else None
            ),
            "top_songs": [{"title": t, "count": c} for t, c in top_songs],
            "effective_type_counts": etype_counts,
        }
    return types


# ---------------------------------------------------------------------------
# Cluster sampler HTML
# ---------------------------------------------------------------------------

_COLORS = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
    "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
    "#8cd17d", "#b6992d", "#f1ce63", "#a0cbe8", "#ffbe7d",
    "#d4a6c8", "#86bcb6", "#d37295", "#fabfd2", "#b9ac8e",
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00",
    "#a65628", "#f781bf", "#999999", "#66c2a5", "#fc8d62",
    "#8da0cb", "#e78ac3", "#a6d854", "#ffd92f", "#e5c494",
    "#b3b3b3", "#1b9e77", "#d95f02", "#7570b3", "#e7298a",
]

_ETYPE_COLORS = {
    "original": "#d4e6f1",
    "cover": "#fde8c8",
    "jam": "#d5f0d5",
    "non-musical": "#fad4d4",
    "unreviewed": "#e8e8e8",
}


def _audio_url(audio_path: str | None, start: float, end: float, port: int | None) -> str | None:
    if not audio_path:
        return None
    if port is not None:
        return f"http://127.0.0.1:{port}/{audio_path}#t={start:.1f},{end:.1f}"
    processed_root = os.environ.get("PROCESSED_ROOT", "")
    if processed_root:
        return f"file://{Path(processed_root).expanduser() / audio_path}#t={start:.1f},{end:.1f}"
    return None


def _build_cluster_sampler_html(
    passages: list[dict],
    passage_types: dict,
    config: dict,
    n_samples: int,
    port: int | None,
) -> str:
    random.seed(42)
    by_cluster: dict[int, list[dict]] = defaultdict(list)
    for p in passages:
        by_cluster[p["passage_type"]].append(p)

    def _etype_badge(etype: str) -> str:
        bg = _ETYPE_COLORS.get(etype, "#e8e8e8")
        return f'<span class="eb" style="background:{bg}">{etype}</span>'

    blocks: list[str] = []
    for cluster_id in sorted(by_cluster):
        group = by_cluster[cluster_id]
        sample = random.sample(group, min(n_samples, len(group)))
        color = _COLORS[cluster_id % len(_COLORS)]
        stats = passage_types.get(str(cluster_id), {})

        etype_counts = stats.get("effective_type_counts", {})
        dominant_etype = max(etype_counts, key=etype_counts.get) if etype_counts else "—"
        mean_dur = stats.get("mean_duration") or 0
        mean_rms = stats.get("mean_rms")
        rms_str = f"{20 * np.log10(mean_rms):.1f} dB" if mean_rms and mean_rms > 0 else "—"
        top_songs = stats.get("top_songs", [])
        top_songs_str = (
            ", ".join(f"{s['title']} ({s['count']})" for s in top_songs[:3])
            if top_songs else "—"
        )

        passage_items: list[str] = []
        for p in sample:
            url = _audio_url(p.get("audio_path"), p["start_seconds"], p["end_seconds"], port)
            title = p.get("song_title") or p.get("recording_title") or f"rec {p['recording_id']}"
            date = p.get("session_date") or "?"
            etype = p.get("effective_type", "unreviewed")
            t0, t1 = int(p["start_seconds"]), int(p["end_seconds"])
            dur_str = f"{p['duration']:.0f}s"
            audio_tag = (
                f'<audio controls preload="none" src="{url}" '
                f'style="width:100%;height:32px;margin-top:4px"></audio>'
                if url else '<em style="color:#aaa">no audio</em>'
            )
            passage_items.append(
                f'<div class="p">'
                f'<div class="pm">{date} — {title} [{t0}s–{t1}s, {dur_str}]'
                f" {_etype_badge(etype)}</div>"
                f"{audio_tag}"
                f"</div>"
            )

        etype_dist = " · ".join(
            f"{et}: {n}" for et, n in sorted(etype_counts.items(), key=lambda x: -x[1])
        )
        blocks.append(
            f'<div class="cluster">'
            f'<div class="ch" style="border-left:4px solid {color}">'
            f'<span class="ci">Cluster {cluster_id}</span>'
            f'<span class="cc">{len(group)} passages</span>'
            f'<span class="cm">~{mean_dur:.0f}s · {rms_str} · {dominant_etype}</span>'
            f"</div>"
            f'<div class="ts">Songs: {top_songs_str}</div>'
            f'<div class="et">{etype_dist}</div>'
            f"{''.join(passage_items)}"
            f"</div>"
        )

    method = config.get("method", "?")
    run_name = config.get("run_name", "?")
    k = config.get("k", "?")
    source = config.get("source_run", "?")

    css = """<style>
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:20px;background:#f8f9fa}
h1{font-size:1.3em;color:#333;margin-bottom:4px}
.meta{color:#666;font-size:.82em;margin-bottom:24px}
.cluster{background:#fff;border:1px solid #ddd;border-radius:8px;margin-bottom:24px;padding:16px 20px}
.ch{display:flex;align-items:baseline;gap:12px;padding-left:10px;margin-bottom:6px}
.ci{font-size:1.05em;font-weight:600;color:#222}
.cc{font-size:.85em;color:#888}
.cm{font-size:.8em;color:#555;background:#f0f0f0;padding:2px 8px;border-radius:4px}
.ts{font-size:.78em;color:#777;margin-bottom:2px;padding-left:10px}
.et{font-size:.75em;color:#999;margin-bottom:10px;padding-left:10px}
.p{border-top:1px solid #f0f0f0;padding:8px 0}
.pm{font-size:.8em;color:#666}
.eb{font-size:.72em;padding:1px 6px;border-radius:3px;display:inline-block;margin-left:4px}
</style>"""

    meta_parts = [
        f"method: <strong>{method}</strong>",
        f"source: <strong>{source}</strong>",
        f"K: <strong>{k}</strong>",
    ]
    if method == "trajectory":
        meta_parts.append(
            f"features: <strong>{TRAJECTORY_N_POINTS}×{TRAJECTORY_PCA_DIM}d traj + 4d SSM + 6d scalar</strong>"
        )

    return (
        f"<!DOCTYPE html><html lang='en'>"
        f"<head><meta charset='UTF-8'>"
        f"<title>Passage clusters — {run_name}</title>"
        f"{css}</head><body>"
        f"<h1>Passage clusters — {run_name}</h1>"
        f"<div class='meta'>"
        f"{' · '.join(meta_parts)} · "
        f"samples/cluster: <strong>{n_samples}</strong>"
        f"</div>"
        f"{''.join(blocks)}"
        f"</body></html>"
    )


# ---------------------------------------------------------------------------
# Write outputs for a single (method, K) run
# ---------------------------------------------------------------------------

def _write_run(
    method: str,
    k: int,
    labels: np.ndarray,
    passages: list[dict],
    passage_embeddings: np.ndarray,
    boundary_features: list[dict],
    feature_matrix: np.ndarray | None,
    affinity_matrix: np.ndarray | None,
    source_run: str,
    output_dir: Path,
    n_samples: int,
    port: int | None = None,
) -> Path:
    run_name = f"{source_run}-{method[:4]}-k{k}" if k > 0 else f"{source_run}-{method[:4]}-hdb"
    out_dir = output_dir / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    updated_passages = [
        {**p, "passage_type": int(label)}
        for p, label in zip(passages, labels)
    ]
    (out_dir / "passages.json").write_text(
        json.dumps(updated_passages, separators=(",", ":"))
    )

    passage_types = _build_passage_types(updated_passages)
    (out_dir / "passage_types.json").write_text(json.dumps(passage_types, indent=2))

    (out_dir / "passage_ids.json").write_text(
        json.dumps([p["passage_id"] for p in updated_passages])
    )

    np.save(str(out_dir / "passage_embeddings.npy"), passage_embeddings)

    if feature_matrix is not None:
        np.save(str(out_dir / "passage_features.npy"), feature_matrix)

    if affinity_matrix is not None:
        np.save(str(out_dir / "affinity_matrix.npy"), affinity_matrix)

    (out_dir / "boundary_features.json").write_text(
        json.dumps(boundary_features, separators=(",", ":"))
    )

    sil = None
    if feature_matrix is not None and len(passages) > k:
        try:
            sil = silhouette_score(
                feature_matrix, labels,
                sample_size=min(len(passages), 2000),
                random_state=42,
            )
        except Exception:
            pass
    elif affinity_matrix is not None and len(passages) > k:
        try:
            dist_matrix = 1.0 - affinity_matrix
            np.fill_diagonal(dist_matrix, 0.0)
            sil = silhouette_score(
                dist_matrix, labels,
                metric="precomputed",
                sample_size=min(len(passages), 2000),
                random_state=42,
            )
        except Exception:
            pass

    config = {
        "method": method,
        "run_name": run_name,
        "source_run": source_run,
        "k": k,
        "n_clusters": k,
        "n_passages": len(updated_passages),
        "silhouette": round(sil, 4) if sil is not None else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if method == "trajectory":
        config["trajectory_n_points"] = TRAJECTORY_N_POINTS
        config["trajectory_pca_dim"] = TRAJECTORY_PCA_DIM
    (out_dir / "config.json").write_text(json.dumps(config, indent=2))

    sampler_html = _build_cluster_sampler_html(
        updated_passages, passage_types, config, n_samples, port
    )
    (out_dir / "cluster_sampler.html").write_text(sampler_html)

    log.info(
        "%s K=%d → %s  (silhouette=%.4f)" if sil else "%s K=%d → %s  (silhouette=N/A)",
        method, k, out_dir, sil or 0,
    )
    return out_dir


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _run(args: argparse.Namespace) -> list[Path]:
    source_dir = args.output_dir / args.source
    if not source_dir.is_dir():
        log.error("Source run not found: %s", source_dir)
        raise SystemExit(1)

    passages_path = source_dir / "passages.json"
    embeddings_path = source_dir / "passage_embeddings.npy"
    if not passages_path.exists() or not embeddings_path.exists():
        log.error("passages.json / passage_embeddings.npy not found in %s", source_dir)
        raise SystemExit(1)

    log.info("Loading passages from %s…", source_dir)
    passages = json.loads(passages_path.read_text())
    passage_embeddings = np.load(str(embeddings_path))
    log.info("Loaded %d passages, embeddings shape=%s", len(passages), passage_embeddings.shape)

    all_segment_ids: set[int] = set()
    for p in passages:
        all_segment_ids.update(p["segment_ids"])
    log.info("Loading %d unique segment records from DB…", len(all_segment_ids))

    db = SessionLocal()
    try:
        seg_by_id = _load_segments_for_ids(db, all_segment_ids)
    finally:
        db.close()
    log.info("Loaded %d segment records", len(seg_by_id))

    boundary_features = _build_boundary_features(passages, seg_by_id)

    precomputed: dict[str, np.ndarray] = {}

    if "trajectory" in args.methods:
        log.info("=== Building CLAP trajectory features ===")
        precomputed["trajectory"] = _build_trajectory_features(passages, seg_by_id)

    if "spectral" in args.methods:
        log.info("=== Building cross-similarity affinity matrix ===")
        precomputed["spectral_affinity"] = _build_affinity_matrix(passages, seg_by_id)

    if "mert" in args.methods:
        log.info("=== Building MERT trajectory features ===")
        precomputed["mert"] = _build_mert_trajectory_features(passages, seg_by_id)

    if "handcrafted" in args.methods:
        log.info("=== Building handcrafted features ===")
        precomputed["handcrafted"] = _build_handcrafted_features(passages, seg_by_id)

    if "combined" in args.methods:
        log.info("=== Building combined MERT + handcrafted features ===")
        precomputed["combined"] = _build_combined_features(passages, seg_by_id)

    algorithms = args.algorithms if hasattr(args, "algorithms") else ["kmeans"]
    out_dirs: list[Path] = []

    def _do_cluster(method: str, feat: np.ndarray, k: int, algo: str) -> None:
        if algo == "kmeans":
            log.info("--- %s kmeans K=%d ---", method, k)
            labels = _cluster_trajectory(feat, k)
        else:
            log.info("--- %s hdbscan ---", method)
            labels = _cluster_hdbscan(feat)
            k = len(set(labels)) - (1 if -1 in labels else 0)

        out_dir = _write_run(
            method=f"{method}-{algo}" if algo != "kmeans" else method,
            k=k, labels=labels,
            passages=passages, passage_embeddings=passage_embeddings,
            boundary_features=boundary_features,
            feature_matrix=feat, affinity_matrix=None,
            source_run=args.source, output_dir=args.output_dir,
            n_samples=args.n_samples,
        )
        out_dirs.append(out_dir)

    for algo in algorithms:
        if algo == "hdbscan":
            for method_name in ["trajectory", "mert", "handcrafted", "combined"]:
                if method_name in args.methods and method_name in precomputed:
                    _do_cluster(method_name, precomputed[method_name], 0, "hdbscan")
        else:
            for k in args.k:
                if "trajectory" in args.methods and "trajectory" in precomputed:
                    _do_cluster("trajectory", precomputed["trajectory"], k, "kmeans")
                if "mert" in args.methods and "mert" in precomputed:
                    _do_cluster("mert", precomputed["mert"], k, "kmeans")
                if "handcrafted" in args.methods and "handcrafted" in precomputed:
                    _do_cluster("handcrafted", precomputed["handcrafted"], k, "kmeans")
                if "combined" in args.methods and "combined" in precomputed:
                    _do_cluster("combined", precomputed["combined"], k, "kmeans")

                if "spectral" in args.methods and "spectral_affinity" in precomputed:
                    log.info("--- spectral K=%d ---", k)
                    labels = _cluster_spectral(precomputed["spectral_affinity"], k)
                    out_dir = _write_run(
                        method="spectral", k=k, labels=labels,
                        passages=passages, passage_embeddings=passage_embeddings,
                        boundary_features=boundary_features,
                        feature_matrix=None, affinity_matrix=precomputed["spectral_affinity"],
                        source_run=args.source, output_dir=args.output_dir,
                        n_samples=args.n_samples,
                    )
                    out_dirs.append(out_dir)

    return out_dirs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_k(s: str) -> list[int]:
    return [int(v.strip()) for v in s.split(",")]


def _parse_methods(s: str) -> list[str]:
    valid = {"trajectory", "spectral", "mert", "handcrafted", "combined"}
    methods = [m.strip() for m in s.split(",")]
    for m in methods:
        if m not in valid:
            raise argparse.ArgumentTypeError(f"Unknown method: {m}. Choose from: {valid}")
    return methods


def _parse_algorithms(s: str) -> list[str]:
    valid = {"kmeans", "hdbscan"}
    algos = [a.strip() for a in s.split(",")]
    for a in algos:
        if a not in valid:
            raise argparse.ArgumentTypeError(f"Unknown algorithm: {a}. Choose from: {valid}")
    return algos


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Acoustic passage clustering from SSM passage runs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--source", required=True, metavar="RUN",
        help="Source passage run name under --output-dir (e.g. ssm-k8-t0p5)",
    )
    p.add_argument(
        "--method", type=_parse_methods, default=["trajectory"],
        dest="methods", metavar="METHOD[,METHOD]",
        help="Clustering method(s): trajectory, spectral, mert, handcrafted, combined (default: trajectory)",
    )
    p.add_argument(
        "--algorithm", type=_parse_algorithms, default=["kmeans"],
        dest="algorithms", metavar="ALGO[,ALGO]",
        help="Clustering algorithm(s): kmeans, hdbscan (default: kmeans)",
    )
    p.add_argument(
        "--k", type=_parse_k, default=DEFAULT_K, metavar="K[,K,...]",
        help="K value(s) for clustering (default: 20)",
    )
    p.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, metavar="DIR",
        help=f"Root output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    p.add_argument(
        "--n-samples", type=int, default=DEFAULT_N_SAMPLES, metavar="N",
        help="Passages per cluster in sampler HTML (default: 8)",
    )
    p.add_argument(
        "--explore", action="store_true",
        help="Generate cluster sampler HTML, start audio server, open in browser",
    )
    p.add_argument(
        "--port", type=int, default=DEFAULT_PORT, metavar="PORT",
        help=f"Preferred audio server port (default: {DEFAULT_PORT}; next free chosen if busy)",
    )
    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = _parse_args()
    out_dirs = _run(args)

    if args.explore and out_dirs:
        import webbrowser

        out_dir = out_dirs[-1]
        passages = json.loads((out_dir / "passages.json").read_text())
        passage_types = json.loads((out_dir / "passage_types.json").read_text())
        config = json.loads((out_dir / "config.json").read_text())

        audio_server, html_server, audio_port, html_port = start_servers(
            out_dir, args.port
        )
        sampler_html = _build_cluster_sampler_html(
            passages, passage_types, config, args.n_samples, audio_port
        )
        (out_dir / "cluster_sampler.html").write_text(sampler_html)
        log.info("Cluster sampler → %s", (out_dir / "cluster_sampler.html").resolve())

        webbrowser.open(f"http://127.0.0.1:{html_port}/cluster_sampler.html")
        block_until_interrupt(
            audio_server, html_server,
            audio_port=audio_port,
            html_port=html_port,
        )
    else:
        log.info("Done. Run with --explore to open the cluster sampler in the browser.")


if __name__ == "__main__":
    main()
