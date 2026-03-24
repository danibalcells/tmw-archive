"""FAISS flat index for CLAP embedding nearest-neighbour queries.

Index type: IndexIDMap wrapping IndexFlatIP (inner-product / cosine similarity
after L2 normalization). IDs in the index correspond to Segment.id in SQLite,
so results map directly back to DB rows without a secondary lookup table.

Public API
----------
build_index(db)
    Read all non-null Segment.clap_embedding rows, normalize, and return a
    populated faiss.IndexIDMap. Typically takes a few seconds for ~24K segments.

save_index(index, path)
    Serialize the index to disk (FAISS binary format).

load_index(path)
    Deserialize from disk. Returns None if the file doesn't exist.

search(index, query_vec, k)
    Return (distances, segment_ids) for the k nearest neighbours of query_vec.
    query_vec is a raw (512,) float32 array; normalization is applied here.
"""

from __future__ import annotations

from pathlib import Path

import faiss
import numpy as np
from sqlalchemy.orm import Session as DBSession

from pipeline.db.models import Segment
from pipeline.features.clap_embeddings import EMBEDDING_DIM, unpack_embedding

DEFAULT_INDEX_PATH = Path("data/clap.index")


def build_index(db: DBSession) -> faiss.IndexIDMap:
    """Build a FAISS IndexFlatIP over all segments with a non-null clap_embedding.

    Embeddings are L2-normalized before insertion so that inner product equals
    cosine similarity at query time.
    """
    rows = (
        db.query(Segment.id, Segment.clap_embedding)
        .filter(Segment.clap_embedding.is_not(None))
        .all()
    )

    if not rows:
        flat = faiss.IndexFlatIP(EMBEDDING_DIM)
        return faiss.IndexIDMap(flat)

    ids = np.array([r.id for r in rows], dtype=np.int64)
    vecs = np.stack([unpack_embedding(r.clap_embedding) for r in rows])
    faiss.normalize_L2(vecs)

    flat = faiss.IndexFlatIP(EMBEDDING_DIM)
    index = faiss.IndexIDMap(flat)
    index.add_with_ids(vecs, ids)
    return index


def save_index(index: faiss.IndexIDMap, path: Path = DEFAULT_INDEX_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(path))


def load_index(path: Path = DEFAULT_INDEX_PATH) -> faiss.IndexIDMap | None:
    path = Path(path)
    if not path.exists():
        return None
    return faiss.read_index(str(path))


def search(
    index: faiss.IndexIDMap,
    query_vec: np.ndarray,
    k: int = 10,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (cosine_scores, segment_ids) for the k nearest neighbours.

    query_vec: (512,) float32 — raw embedding, normalization applied here.
    """
    vec = query_vec.astype(np.float32).reshape(1, -1).copy()
    faiss.normalize_L2(vec)
    distances, ids = index.search(vec, k)
    return distances[0], ids[0]
