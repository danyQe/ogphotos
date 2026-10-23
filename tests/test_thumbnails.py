from io import BytesIO
from pathlib import Path

from PIL import Image

from app.services.thumbnails import make_thumbnail
from tests.imgutil import image_bytes


def _src(tmp_path: Path, data: bytes) -> Path:
    p = tmp_path / "src.bin"
    p.write_bytes(data)
    return p


def test_thumbnail_downscales_to_long_edge(tmp_path: Path) -> None:
    dest = tmp_path / "thumbs" / "1.jpg"
    make_thumbnail(_src(tmp_path, image_bytes(size=(1200, 600))), dest, 400)
    with Image.open(dest) as t:
        assert (t.format, t.size) == ("JPEG", (400, 200))


def test_thumbnail_applies_exif_orientation(tmp_path: Path) -> None:
    dest = tmp_path / "t.jpg"
    make_thumbnail(_src(tmp_path, image_bytes(size=(800, 400), orientation=6)), dest, 400)
    with Image.open(dest) as t:
        assert t.size == (200, 400)  # rotated to portrait


def test_thumbnail_flattens_transparency(tmp_path: Path) -> None:
    img = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    buf = BytesIO()
    img.save(buf, "PNG")
    dest = tmp_path / "t.jpg"
    make_thumbnail(_src(tmp_path, buf.getvalue()), dest, 400)
    with Image.open(dest) as t:
        r, g, b = t.convert("RGB").getpixel((25, 25))
        assert min(r, g, b) > 240  # white, not black


def test_thumbnail_from_heic(tmp_path: Path) -> None:
    dest = tmp_path / "t.jpg"
    make_thumbnail(_src(tmp_path, image_bytes(fmt="HEIF", size=(100, 50))), dest, 400)
    with Image.open(dest) as t:
        assert t.size == (100, 50)  # never upscaled
