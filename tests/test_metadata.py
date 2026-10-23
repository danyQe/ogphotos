from datetime import datetime
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.services.metadata import (
    UnsupportedMedia,
    parse_exif_datetime,
    probe,
    resolve_taken_at,
)
from tests.imgutil import image_bytes, mp4_bytes


def _write(tmp_path: Path, data: bytes) -> Path:
    p = tmp_path / "upload.part"
    p.write_bytes(data)
    return p


def test_probe_jpeg_with_exif_date(tmp_path: Path) -> None:
    info = probe(_write(tmp_path, image_bytes(exif_date="2021:05:06 07:08:09")), "a.jpg")
    assert (info.media_type, info.mime, info.width, info.height) == ("image", "image/jpeg", 64, 48)
    assert info.exif_taken_at == datetime(2021, 5, 6, 7, 8, 9)


@pytest.mark.parametrize(
    ("fmt", "mime"),
    [("PNG", "image/png"), ("WEBP", "image/webp"), ("GIF", "image/gif"), ("HEIF", "image/heic")],
)
def test_probe_other_image_formats(tmp_path: Path, fmt: str, mime: str) -> None:
    info = probe(_write(tmp_path, image_bytes(fmt=fmt)), "x.img")
    assert (info.media_type, info.mime, info.exif_taken_at) == ("image", mime, None)


def test_probe_rejects_unsupported_image_format(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedMedia, match="BMP"):
        probe(_write(tmp_path, image_bytes(fmt="BMP")), "a.bmp")


@pytest.mark.parametrize("data", [b"", b"not an image at all"])
def test_probe_rejects_garbage(tmp_path: Path, data: bytes) -> None:
    with pytest.raises(UnsupportedMedia):
        probe(_write(tmp_path, data), "a.jpg")


def test_probe_mp4_and_mov(tmp_path: Path) -> None:
    assert probe(_write(tmp_path, mp4_bytes()), "v.mp4").mime == "video/mp4"
    mov = probe(_write(tmp_path, mp4_bytes(b"qt  ")), "v.MOV")
    assert (mov.media_type, mov.mime, mov.width) == ("video", "video/quicktime", None)


def test_probe_rejects_fake_video(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedMedia):
        probe(_write(tmp_path, b"\x00" * 64), "v.mp4")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2020:01:02 03:04:05", datetime(2020, 1, 2, 3, 4, 5)),
        ("2020:01:02 03:04:05\x00", datetime(2020, 1, 2, 3, 4, 5)),
        ("0000:00:00 00:00:00", None),
        ("garbage", None),
        (None, None),
        (12345, None),
    ],
)
def test_parse_exif_datetime(raw: object, expected: datetime | None) -> None:
    assert parse_exif_datetime(raw) == expected


def test_resolve_taken_at_order() -> None:
    a, b, c, now = (datetime(y, 1, 1) for y in (2001, 2002, 2003, 2024))
    assert resolve_taken_at(a, b, c, now) == (a, "exif")
    assert resolve_taken_at(None, b, c, now) == (b, "sidecar")
    assert resolve_taken_at(None, None, c, now) == (c, "mtime")
    assert resolve_taken_at(None, None, None, now) == (now, "upload")


def test_probe_accepts_multi_picture_jpeg(tmp_path: Path) -> None:
    # iPhone HDR / Android Ultra HDR JPEGs carry a gain map as a second MPF frame
    buf = BytesIO()
    first = Image.new("RGB", (64, 48), (10, 20, 30))
    first.save(buf, "MPO", save_all=True, append_images=[Image.new("RGB", (32, 24))])
    info = probe(_write(tmp_path, buf.getvalue()), "IMG_HDR.jpg")
    assert (info.media_type, info.mime, info.width, info.height) == ("image", "image/jpeg", 64, 48)


def test_pillow_default_pixel_limit_is_disabled() -> None:
    # Pillow's built-in bomb guard (~179 MP) rejects real panoramas; probe() enforces
    # settings.max_image_pixels instead.
    import app.services  # noqa: F401

    assert Image.MAX_IMAGE_PIXELS is None


def test_probe_rejects_images_over_max_pixels(tmp_path: Path) -> None:
    path = _write(tmp_path, image_bytes(size=(64, 48)))
    assert probe(path, "a.jpg", max_pixels=64 * 48).width == 64
    with pytest.raises(UnsupportedMedia, match="3,072 pixels exceeds the 3,071 pixel limit"):
        probe(path, "a.jpg", max_pixels=64 * 48 - 1)
