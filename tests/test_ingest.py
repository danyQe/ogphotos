import hashlib
import json
from datetime import datetime
from io import BytesIO

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import File, Setting
from app.services import ingest
from app.services.ingest import ingest_file, library_relpath
from app.services.phash_index import PhashIndex
from tests.imgutil import image_bytes, mp4_bytes, photo_bytes

DATED = image_bytes(exif_date="2021:05:06 07:08:09")


def _ingest(db: sessionmaker[Session], settings: Settings, data: bytes, name: str, **kw):
    with db() as s:
        return ingest_file(s, settings, BytesIO(data), name, **kw)


def _rows(db: sessionmaker[Session]) -> list[File]:
    with db() as s:
        return list(s.scalars(select(File)))


def _library_files(settings: Settings) -> list[str]:
    return sorted(
        p.relative_to(settings.data_dir).as_posix()
        for p in settings.library_dir.rglob("*")
        if p.is_file()
    )


def test_new_image_is_stored(db, settings: Settings) -> None:
    r = _ingest(db, settings, DATED, "Photos from 2021/photo.JPG")
    sha = hashlib.sha256(DATED).hexdigest()
    assert (r.status, r.filename) == ("new", "Photos from 2021/photo.JPG")
    [row] = _rows(db)
    assert row.id == r.file_id
    assert row.stored_path == f"library/2021/photo__{sha[:8]}.jpg"
    assert (row.original_name, row.sha256, row.mime) == ("photo.JPG", sha, "image/jpeg")
    assert (row.width, row.height, row.size_bytes) == (64, 48, len(DATED))
    assert (row.taken_at, row.taken_at_source) == (datetime(2021, 5, 6, 7, 8, 9), "exif")
    assert (row.analysis_status, row.is_trashed) == ("pending", False)
    assert row.phash is not None and len(row.phash) == 16
    assert (settings.data_dir / row.stored_path).read_bytes() == DATED
    assert (settings.thumbs_dir / f"{row.id}.jpg").is_file()
    assert list(settings.tmp_dir.iterdir()) == []


def test_same_bytes_twice_in_one_session(db, settings: Settings) -> None:
    with db() as s:
        first = ingest_file(s, settings, BytesIO(DATED), "a.jpg")
        second = ingest_file(s, settings, BytesIO(DATED), "album/a copy.jpg")
    assert first.status == "new"
    assert second.status == "exact_duplicate"
    assert second.existing_file_id == first.file_id
    assert second.file_id is None
    assert "a.jpg" in second.message and "2021" in second.message
    assert len(_rows(db)) == 1
    assert len(_library_files(settings)) == 1
    assert list(settings.tmp_dir.iterdir()) == []


def test_duplicate_with_sidecar_fixes_a_file_time_date(db, settings: Settings) -> None:
    data = photo_bytes(3)  # no EXIF date
    first = _ingest(db, settings, data, "a.jpg", mtime=datetime(2026, 1, 1))
    sidecar = json.dumps({"photoTakenTime": {"timestamp": "1561982400"}}).encode()
    again = _ingest(db, settings, data, "a.jpg", sidecar=sidecar)
    assert again.status == "exact_duplicate" and again.date_fixed
    assert "date updated from sidecar" in again.message and "(2019)" in again.message
    [row] = _rows(db)
    assert row.id == first.file_id and row.taken_at_source == "sidecar"
    assert row.taken_at == datetime(2019, 7, 1, 12, 0)
    assert row.stored_path.startswith("library/2026/")  # the file itself is not moved

    later = json.dumps({"photoTakenTime": {"timestamp": "1600000000"}}).encode()
    third = _ingest(db, settings, data, "a.jpg", sidecar=later)
    assert not third.date_fixed and _rows(db)[0].taken_at.year == 2019  # sidecar dates stick


def test_duplicate_of_trashed_file(db, settings: Settings) -> None:
    first = _ingest(db, settings, DATED, "a.jpg")
    with db() as s:
        s.get(File, first.file_id).is_trashed = True
        s.commit()
    r = _ingest(db, settings, DATED, "a.jpg")
    assert r.status == "exact_duplicate"
    assert r.existing_is_trashed is True
    assert "Trash" in r.message


@pytest.mark.parametrize(
    "data",
    [b"", b"definitely not a jpeg", image_bytes(fmt="BMP")],
    ids=["empty", "garbage", "bmp"],
)
def test_invalid_file_leaves_nothing_behind(db, settings: Settings, data: bytes) -> None:
    r = _ingest(db, settings, data, "bad.jpg")
    assert r.status == "error"
    assert r.message
    assert _rows(db) == []
    assert _library_files(settings) == []
    assert list(settings.tmp_dir.iterdir()) == []


def test_unsafe_filename_is_sanitized(db, settings: Settings) -> None:
    r = _ingest(db, settings, DATED, '..\\..\\evil:na"me?.jpg')
    assert r.status == "new"
    [row] = _rows(db)
    assert row.original_name == 'evil:na"me?.jpg'
    assert row.stored_path.startswith("library/2021/evil_na_me___")
    stored = (settings.data_dir / row.stored_path).resolve()
    assert settings.library_dir.resolve() in stored.parents


def test_sidecar_date_used_when_no_exif(db, settings: Settings) -> None:
    sidecar = json.dumps({"photoTakenTime": {"timestamp": "1546300800"}}).encode()
    _ingest(db, settings, image_bytes(), "a.png", sidecar=sidecar)
    [row] = _rows(db)
    assert (row.taken_at, row.taken_at_source) == (datetime(2019, 1, 1), "sidecar")
    assert row.stored_path.startswith("library/2019/")


def test_mtime_then_upload_fallback(db, settings: Settings) -> None:
    _ingest(db, settings, image_bytes(color=(1, 2, 3)), "a.png", mtime=datetime(2017, 3, 4))
    _ingest(db, settings, image_bytes(color=(4, 5, 6)), "b.png")
    a, b = sorted(_rows(db), key=lambda f: f.original_name)
    assert (a.taken_at_source, a.taken_at.year) == ("mtime", 2017)
    assert b.taken_at_source == "upload"


def test_video_stored_without_analysis(db, settings: Settings) -> None:
    r = _ingest(db, settings, mp4_bytes(), "clip.MP4", mtime=datetime(2020, 1, 1))
    assert r.status == "new"
    [row] = _rows(db)
    assert (row.media_type, row.mime, row.analysis_status) == ("video", "video/mp4", "skipped")
    assert row.phash is None
    assert not (settings.thumbs_dir / f"{row.id}.jpg").exists()


def test_thumbnail_failure_rolls_back(db, settings: Settings, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(ingest, "make_thumbnail", boom)
    r = _ingest(db, settings, DATED, "a.jpg")
    assert r.status == "error" and "disk full" in r.message
    assert _rows(db) == []
    assert _library_files(settings) == []
    assert list(settings.tmp_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("name", "expected_tail"),
    [("IMG.JPG", "IMG__abcdef12.jpg"), ("noext", "noext__abcdef12"), ("...", "file__abcdef12")],
)
def test_library_relpath(name: str, expected_tail: str) -> None:
    assert library_relpath(name, "abcdef1234" + "0" * 54, 2020) == f"library/2020/{expected_tail}"


def test_max_image_pixels_comes_from_settings(db, settings: Settings) -> None:
    small = settings.model_copy(update={"max_image_pixels": 100})
    r = _ingest(db, small, DATED, "big.jpg")
    assert r.status == "error" and "pixel limit" in r.message
    assert settings.max_image_pixels == 400_000_000


def test_resized_copy_is_similar_exists(db, settings: Settings) -> None:
    index = PhashIndex()
    first = _ingest(db, settings, photo_bytes(1, size=(1600, 1200)), "big.jpg", phash_index=index)
    other = _ingest(db, settings, photo_bytes(2), "other.jpg", phash_index=index)
    copy = _ingest(db, settings, photo_bytes(1, size=(400, 300)), "small.jpg", phash_index=index)
    assert (first.status, other.status) == ("new", "new")
    assert copy.status == "similar_exists"
    assert copy.similar_file_ids == [first.file_id]
    assert "similar" in copy.message.lower()
    # a near-duplicate is still stored and indexed; the user decides later
    assert copy.file_id is not None
    assert len(_rows(db)) == 3 and len(_library_files(settings)) == 3
    assert len(index) == 3


def test_similar_matches_capped_closest_first(db, settings: Settings) -> None:
    index = PhashIndex()
    ids = [
        _ingest(db, settings, photo_bytes(1, size=(300 + i, 225)), f"{i}.jpg", phash_index=index)
        for i in range(settings.similar_max_matches + 2)
    ]
    last = ids[-1]
    assert last.status == "similar_exists"
    assert len(last.similar_file_ids) == settings.similar_max_matches
    assert set(last.similar_file_ids) <= {r.file_id for r in ids[:-1]}


def test_phash_threshold_comes_from_settings_table(db, settings: Settings) -> None:
    with db() as s:
        s.get(Setting, "phash_max_distance").value = "64"  # everything is similar
        s.commit()
    index = PhashIndex()
    first = _ingest(db, settings, photo_bytes(1), "a.jpg", phash_index=index)
    r = _ingest(db, settings, photo_bytes(2), "b.jpg", phash_index=index)
    assert r.status == "similar_exists" and r.similar_file_ids == [first.file_id]


def test_failed_and_duplicate_ingests_not_indexed(db, settings: Settings, monkeypatch) -> None:
    index = PhashIndex()
    _ingest(db, settings, DATED, "a.jpg", phash_index=index)
    _ingest(db, settings, DATED, "again.jpg", phash_index=index)
    monkeypatch.setattr(ingest, "make_thumbnail", lambda *_a, **_k: 1 / 0)
    r = _ingest(db, settings, photo_bytes(5), "b.jpg", phash_index=index)
    assert r.status == "error"
    assert len(index) == 1
