import threading
from datetime import datetime
from pathlib import Path

from PIL import Image

from app.config import Settings
from app.models import File
from app.services.thumbnails import make_video_thumbnail
from app.services.video_backfill import backfill_videos
from tests.imgutil import mp4_bytes, mp4_header_bytes, video_bytes
from tests.test_video_meta import _ingest


def test_thumbnail_from_real_video(tmp_path: Path) -> None:
    src = tmp_path / "v.mp4"
    src.write_bytes(video_bytes(tmp_path))
    dest = tmp_path / "t.jpg"
    assert make_video_thumbnail(src, dest, 400) is True
    with Image.open(dest) as img:
        assert img.size == (160, 120)
        r, g, b = img.convert("RGB").getpixel((10, 60))
        assert r > 150 and g < 100 and b < 100  # left half red: channels not swapped


def test_non_ascii_path(tmp_path: Path) -> None:
    src = tmp_path / "vidéo_日本.mp4"
    src.write_bytes(video_bytes(tmp_path))
    assert make_video_thumbnail(src, tmp_path / "t.jpg", 400) is True


def test_broken_video_gives_no_thumbnail(tmp_path: Path) -> None:
    src = tmp_path / "v.mp4"
    src.write_bytes(mp4_bytes())
    assert make_video_thumbnail(src, tmp_path / "t.jpg", 400) is False
    assert not (tmp_path / "t.jpg").exists()


def test_upload_makes_video_thumbnail(db, settings: Settings, tmp_path: Path) -> None:
    _, row = _ingest(db, settings, video_bytes(tmp_path), "clip.mp4")
    assert row.media_type == "video"
    assert (settings.thumbs_dir / f"{row.id}.jpg").is_file()


def test_backfill_fills_old_videos_once(db, settings: Settings) -> None:
    _, row = _ingest(db, settings, mp4_header_bytes(datetime(2019, 5, 1), 7.0), "old.mp4")
    with db() as s:  # as if stored before V1: no duration, file-time date
        f = s.get(File, row.id)
        f.duration_s, f.taken_at, f.taken_at_source = None, datetime(2024, 1, 1), "mtime"
        s.commit()
    assert backfill_videos(db, settings, threading.Event()) == 1
    with db() as s:
        f = s.get(File, row.id)
        assert (f.duration_s, f.taken_at, f.taken_at_source) == (
            7.0,
            datetime(2019, 5, 1),
            "metadata",
        )
    assert backfill_videos(db, settings, threading.Event()) == 0
