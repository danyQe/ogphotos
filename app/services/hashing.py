import hashlib
from pathlib import Path
from typing import BinaryIO

import imagehash
from PIL import Image, ImageOps

CHUNK_SIZE = 1024 * 1024
# pHash shrinks to 32x32 anyway; let JPEG decode at reduced scale for speed
PHASH_DRAFT_SIZE = 256


def sha256_copy(src: BinaryIO, dest: Path, chunk_size: int = CHUNK_SIZE) -> tuple[str, int]:
    """Copy `src` to `dest` while hashing. Returns (sha256 hex digest, bytes written)."""
    h = hashlib.sha256()
    size = 0
    with dest.open("wb") as out:
        while chunk := src.read(chunk_size):
            h.update(chunk)
            out.write(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def phash_hex(path: Path) -> str:
    """64-bit perceptual hash of the image as viewed (EXIF orientation applied), 16 hex chars."""
    with Image.open(path) as img:
        img.draft("RGB", (PHASH_DRAFT_SIZE, PHASH_DRAFT_SIZE))
        return str(imagehash.phash(ImageOps.exif_transpose(img)))


def hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()
