import io
from datetime import datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.models import File
from app.services.ingest import ingest_file
from app.services.video_meta import read_mp4_header
from tests.imgutil import mp4_bytes, mp4_header_bytes, video_bytes

WHEN = datetime(2021, 7, 4, 18, 30, 0)


@pytest.mark.parametrize("version", [0, 1])
@pytest.mark.parametrize("moov_last", [False, True])
def test_reads_creation_time_and_duration(tmp_path: Path, version: int, moov_last: bool) -> None:
    p = tmp_path / "a.mp4"
    p.write_bytes(mp4_header_bytes(WHEN, 42.5, version, moov_last))
    assert read_mp4_header(p) == (WHEN, 42.5)


def test_zero_or_bogus_dates_ignored(tmp_path: Path) -> None:
    p = tmp_path / "a.mp4"
    p.write_bytes(mp4_header_bytes(None, 3.0))
    assert read_mp4_header(p) == (None, 3.0)
    p.write_bytes(mp4_header_bytes(datetime(1970, 1, 1), 3.0))
    assert read_mp4_header(p)[0] is None


def test_garbage_and_truncated_files(tmp_path: Path) -> None:
    p = tmp_path / "a.mp4"
    p.write_bytes(mp4_bytes())
    assert read_mp4_header(p) == (None, None)
    p.write_bytes(mp4_header_bytes(WHEN, 5.0)[:40])
    assert read_mp4_header(p) == (None, None)


def test_real_opencv_video_duration(tmp_path: Path) -> None:
    p = tmp_path / "v.mp4"
    p.write_bytes(video_bytes(tmp_path, seconds=2.0))
    _, duration = read_mp4_header(p)
    assert duration is not None and abs(duration - 2.0) < 0.3


def _ingest(db, settings: Settings, data: bytes, name: str, sidecar: bytes | None = None):
    with db() as s:
        r = ingest_file(
            s, settings, io.BytesIO(data), name, sidecar=sidecar, mtime=datetime(2024, 1, 1)
        )
        return r, s.get(File, r.file_id)


def test_video_date_order(db, settings: Settings) -> None:
    sidecar = b'{"photoTakenTime": {"timestamp": "1600000000"}}'
    _, row = _ingest(db, settings, mp4_header_bytes(WHEN, 9.0), "a.mp4", sidecar)
    assert (row.taken_at, row.taken_at_source, row.duration_s) == (WHEN, "metadata", 9.0)
    _, row = _ingest(db, settings, mp4_header_bytes(None, 9.5), "b.mp4", sidecar)
    assert row.taken_at_source == "sidecar"
    _, row = _ingest(db, settings, mp4_header_bytes(None, 8.0), "c.mov")
    assert row.taken_at_source == "mtime" and row.taken_at == datetime(2024, 1, 1)
    assert row.stored_path.startswith("library/2024/")
