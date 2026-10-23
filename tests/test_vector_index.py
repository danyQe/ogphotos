from datetime import datetime

import numpy as np
import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.models import File
from app.services.vector_index import VectorIndex, from_blob, to_blob


def _unit(*xs: float) -> np.ndarray:
    v = np.array(xs, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_empty_index() -> None:
    assert VectorIndex(3).search(_unit(1, 0, 0), 0.0, 5) == []


def test_search_threshold_order_limit_and_exclude() -> None:
    idx = VectorIndex(2)
    idx.add(1, _unit(1, 0))
    idx.add(2, _unit(1, 1))  # cos 0.707
    idx.add(3, _unit(1, 0.1))  # cos 0.995
    idx.add(4, _unit(0, 1))  # cos 0
    assert len(idx) == 4
    hits = idx.search(_unit(1, 0), 0.7, 10)
    assert [i for i, _ in hits] == [1, 3, 2]
    assert hits[0][1] == pytest.approx(1.0)
    assert [i for i, _ in idx.search(_unit(1, 0), 0.7, 10, exclude_id=1)] == [3, 2]
    assert [i for i, _ in idx.search(_unit(1, 0), 0.7, 1)] == [1]


def test_add_replaces_and_remove() -> None:
    idx = VectorIndex(2)
    idx.add(1, _unit(1, 0))
    idx.add(1, _unit(0, 1))  # re-analysis replaces, no duplicate
    assert len(idx) == 1
    assert idx.search(_unit(0, 1), 0.99, 5)[0][0] == 1
    idx.remove(1)
    idx.remove(42)
    assert len(idx) == 0


def test_get_and_wrong_dim() -> None:
    idx = VectorIndex(2)
    idx.add(7, _unit(1, 0))
    assert np.allclose(idx.get(7), _unit(1, 0))
    assert idx.get(8) is None
    with pytest.raises(ValueError):
        idx.add(9, _unit(1, 0, 0))


def test_blob_roundtrip() -> None:
    v = _unit(1, 2, 3)
    assert np.array_equal(from_blob(to_blob(v)), v)


def test_load_skips_trashed_and_wrong_dim(db: sessionmaker[Session]) -> None:
    def row(sha: str, vec: np.ndarray | None, trashed: bool = False) -> File:
        return File(
            sha256=sha,
            original_name=sha,
            stored_path=sha,
            media_type="image",
            mime="image/jpeg",
            size_bytes=1,
            uploaded_at=datetime(2024, 1, 1),
            embedding=None if vec is None else to_blob(vec),
            is_trashed=trashed,
        )

    with db() as s:
        rows = [
            row("a", _unit(1, 0)),
            row("b", _unit(1, 0), trashed=True),
            row("c", None),
            row("d", _unit(1, 0, 0)),  # stale dim (embed_dim changed) → ignored
        ]
        s.add_all(rows)
        s.commit()
        idx = VectorIndex.load(s, 2)
    assert [i for i, _ in idx.search(_unit(1, 0), 0.5, 5)] == [rows[0].id]
