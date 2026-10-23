import threading
from io import BytesIO

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import File
from app.services.hashing import phash_hex
from app.services.ingest import ingest_file
from app.services.phash_backfill import backfill_phashes
from app.services.phash_index import PhashIndex
from tests.imgutil import mp4_bytes, photo_bytes


def _seed_without_phash(db: sessionmaker[Session], settings: Settings) -> None:
    """Rows as M1 left them: stored images with no pHash."""
    with db() as s:
        for i, data in enumerate([photo_bytes(1), photo_bytes(2), photo_bytes(3)]):
            ingest_file(s, settings, BytesIO(data), f"{i}.jpg")
        ingest_file(s, settings, BytesIO(mp4_bytes()), "v.mp4")
        for row in s.scalars(select(File)):
            row.phash = None
        s.commit()


def test_backfill_hashes_and_indexes(db, settings: Settings) -> None:
    _seed_without_phash(db, settings)
    with db() as s:
        broken = s.scalars(select(File).where(File.original_name == "2.jpg")).one()
        (settings.data_dir / broken.stored_path).unlink()  # file missing → skipped
    index = PhashIndex()
    assert backfill_phashes(db, settings, index, threading.Event(), batch_size=1) == 2
    with db() as s:
        rows = {f.original_name: f for f in s.scalars(select(File))}
    for name in ("0.jpg", "1.jpg"):
        expected = phash_hex(settings.data_dir / rows[name].stored_path)
        assert rows[name].phash == expected
        assert index.search(expected, 0, 5) == [(rows[name].id, 0)]
    assert rows["2.jpg"].phash is None and rows["v.mp4"].phash is None
    assert len(index) == 2


def test_backfill_stops_when_asked(db, settings: Settings) -> None:
    _seed_without_phash(db, settings)
    stop = threading.Event()
    stop.set()
    assert backfill_phashes(db, settings, PhashIndex(), stop) == 0
