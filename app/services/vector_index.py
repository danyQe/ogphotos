import threading

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import File


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


class VectorIndex:
    """In-memory matrix of L2-normalized embeddings; cosine = dot product.

    Holds non-trashed analyzed images only. Thread-safe: writers swap in new arrays,
    readers take a snapshot."""

    def __init__(self, dim: int) -> None:
        self.dim = dim
        self._ids = np.empty(0, dtype=np.int64)
        self._vecs = np.empty((0, dim), dtype=np.float32)
        self._lock = threading.Lock()

    @classmethod
    def load(cls, session: Session, dim: int) -> "VectorIndex":
        rows = session.execute(
            select(File.id, File.embedding).where(
                File.embedding.is_not(None), File.is_trashed.is_(False)
            )
        ).all()
        return cls.from_rows([(r.id, r.embedding) for r in rows], dim)

    @classmethod
    def from_rows(cls, rows: list[tuple[int, bytes]], dim: int) -> "VectorIndex":
        index = cls(dim)
        vecs = [(i, from_blob(b)) for i, b in rows]
        vecs = [(i, v) for i, v in vecs if v.shape == (dim,)]
        if vecs:
            index._ids = np.array([i for i, _ in vecs], dtype=np.int64)
            index._vecs = np.stack([v for _, v in vecs])
        return index

    def ids(self) -> list[int]:
        return [int(i) for i in self._ids]

    def __len__(self) -> int:
        return len(self._ids)

    def add(self, file_id: int, vec: np.ndarray) -> None:
        vec = np.asarray(vec, dtype=np.float32)
        if vec.shape != (self.dim,):
            raise ValueError(f"expected a {self.dim}-d vector, got shape {vec.shape}")
        with self._lock:
            keep = self._ids != file_id
            self._ids = np.append(self._ids[keep], np.int64(file_id))
            self._vecs = np.vstack([self._vecs[keep], vec[None, :]])

    def remove(self, file_id: int) -> None:
        with self._lock:
            keep = self._ids != file_id
            self._ids, self._vecs = self._ids[keep], self._vecs[keep]

    def get(self, file_id: int) -> np.ndarray | None:
        ids, vecs = self._ids, self._vecs
        hit = np.flatnonzero(ids == file_id)
        return vecs[hit[0]] if len(hit) else None

    def search(
        self, vec: np.ndarray, min_cosine: float, limit: int, exclude_id: int | None = None
    ) -> list[tuple[int, float]]:
        """(file_id, cosine) with cosine ≥ `min_cosine`, most similar first, ties by id."""
        with self._lock:
            ids, vecs = self._ids, self._vecs
        cos = vecs @ np.asarray(vec, dtype=np.float32)
        hit = np.flatnonzero((cos >= min_cosine) & (ids != (exclude_id or -1)))
        order = hit[np.lexsort((ids[hit], -cos[hit]))][:limit]
        return [(int(ids[i]), float(cos[i])) for i in order]
