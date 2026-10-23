import threading

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import File


class PhashIndex:
    """In-memory pHash store: file ids + uint64 hashes, Hamming search by bit counting.

    Holds non-trashed images only. Thread-safe; uploads run in a thread pool."""

    def __init__(self) -> None:
        self._ids = np.empty(0, dtype=np.int64)
        self._hashes = np.empty(0, dtype=np.uint64)
        self._lock = threading.Lock()

    @classmethod
    def load(cls, session: Session) -> "PhashIndex":
        rows = session.execute(
            select(File.id, File.phash).where(File.phash.is_not(None), File.is_trashed.is_(False))
        ).all()
        index = cls()
        index._ids = np.array([r.id for r in rows], dtype=np.int64)
        index._hashes = np.array([int(r.phash, 16) for r in rows], dtype=np.uint64)
        return index

    def __len__(self) -> int:
        return len(self._ids)

    def add(self, file_id: int, phash: str) -> None:
        with self._lock:
            self._ids = np.append(self._ids, np.int64(file_id))
            self._hashes = np.append(self._hashes, np.uint64(int(phash, 16)))

    def remove(self, file_id: int) -> None:
        with self._lock:
            keep = self._ids != file_id
            self._ids, self._hashes = self._ids[keep], self._hashes[keep]

    def search(self, phash: str, max_distance: int, limit: int) -> list[tuple[int, int]]:
        """(file_id, distance) of hashes within `max_distance` bits, closest first, ties by id."""
        with self._lock:
            ids, hashes = self._ids, self._hashes
        dist = np.bitwise_count(hashes ^ np.uint64(int(phash, 16)))
        hit = np.flatnonzero(dist <= max_distance)
        order = hit[np.lexsort((ids[hit], dist[hit]))][:limit]
        return [(int(ids[i]), int(dist[i])) for i in order]
