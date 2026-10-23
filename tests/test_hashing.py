import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.services.hashing import hamming, phash_hex, sha256_copy
from tests.imgutil import photo_bytes


def test_sha256_copy_hashes_and_writes(tmp_path: Path) -> None:
    data = b"hello world" * 1000
    dest = tmp_path / "out.bin"
    digest, size = sha256_copy(BytesIO(data), dest, chunk_size=7)
    assert digest == hashlib.sha256(data).hexdigest()
    assert size == len(data)
    assert dest.read_bytes() == data


def test_sha256_copy_empty(tmp_path: Path) -> None:
    digest, size = sha256_copy(BytesIO(b""), tmp_path / "e")
    assert (digest, size) == (hashlib.sha256(b"").hexdigest(), 0)


def _write(tmp_path: Path, data: bytes, name: str = "img") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_phash_resized_copy_is_close(tmp_path: Path) -> None:
    a = phash_hex(_write(tmp_path, photo_bytes(1, size=(1600, 1200)), "a"))
    b = phash_hex(_write(tmp_path, photo_bytes(1, size=(400, 300), fmt="PNG"), "b"))
    assert len(a) == 16 and int(a, 16) >= 0
    assert hamming(a, b) <= 4


def test_phash_different_photos_are_far(tmp_path: Path) -> None:
    a = phash_hex(_write(tmp_path, photo_bytes(1), "a"))
    b = phash_hex(_write(tmp_path, photo_bytes(2), "b"))
    assert hamming(a, b) > 16


def test_phash_applies_exif_orientation(tmp_path: Path) -> None:
    upright = Image.open(BytesIO(photo_bytes(3))).rotate(-90, expand=True)
    buf = BytesIO()
    upright.save(buf, "JPEG")
    # same pixels stored sideways with Orientation=6 ("rotate 90° clockwise to view")
    sideways = Image.open(BytesIO(photo_bytes(3)))
    exif = Image.Exif()
    exif[0x0112] = 6
    buf2 = BytesIO()
    sideways.save(buf2, "JPEG", exif=exif.tobytes())
    a = phash_hex(_write(tmp_path, buf.getvalue(), "a"))
    b = phash_hex(_write(tmp_path, buf2.getvalue(), "b"))
    assert hamming(a, b) <= 4


def test_hamming() -> None:
    assert hamming("0000000000000000", "ffffffffffffffff") == 64
    assert hamming("8000000000000001", "0000000000000000") == 2
