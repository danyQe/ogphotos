from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.models import File
from app.services.phash_index import PhashIndex

ZERO = "0000000000000000"


def _flip(bits: int) -> str:
    """Hash `bits` away from ZERO."""
    return f"{(1 << bits) - 1:016x}"


def test_empty_index_finds_nothing() -> None:
    assert PhashIndex().search(ZERO, 8, 5) == []


def test_search_threshold_order_and_limit() -> None:
    idx = PhashIndex()
    for file_id, bits in [(1, 9), (2, 3), (3, 0), (4, 8), (5, 3), (6, 40)]:
        idx.add(file_id, _flip(bits))
    assert len(idx) == 6
    # closest first, ties by id; 9 and 40 bits away are over the threshold
    assert idx.search(ZERO, 8, 10) == [(3, 0), (2, 3), (5, 3), (4, 8)]
    assert idx.search(ZERO, 8, 2) == [(3, 0), (2, 3)]
    assert idx.search(ZERO, 0, 10) == [(3, 0)]


def test_remove() -> None:
    idx = PhashIndex()
    idx.add(1, ZERO)
    idx.add(2, ZERO)
    idx.remove(1)
    idx.remove(99)  # unknown id is a no-op
    assert idx.search(ZERO, 8, 5) == [(2, 0)]


def test_full_64_bit_hashes() -> None:
    idx = PhashIndex()
    idx.add(1, "ffffffffffffffff")
    assert idx.search("fffffffffffffffe", 1, 5) == [(1, 1)]


def _file(sha: str, phash: str | None, trashed: bool = False, media: str = "image") -> File:
    return File(
        sha256=sha,
        phash=phash,
        original_name=sha,
        stored_path=sha,
        media_type=media,
        mime="image/jpeg",
        size_bytes=1,
        uploaded_at=datetime(2024, 1, 1),
        is_trashed=trashed,
    )


def test_load_skips_trashed_and_unhashed(db: sessionmaker[Session]) -> None:
    with db() as s:
        rows = [
            _file("a", ZERO),
            _file("b", ZERO, trashed=True),
            _file("c", None, media="video"),
            _file("d", _flip(2)),
        ]
        s.add_all(rows)
        s.commit()
        idx = PhashIndex.load(s)
    assert idx.search(ZERO, 8, 5) == [(rows[0].id, 0), (rows[3].id, 2)]
