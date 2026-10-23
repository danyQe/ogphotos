import struct
from datetime import datetime
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

EXIF_IFD = 0x8769
DATETIME_ORIGINAL = 36867
ORIENTATION = 0x0112
MAC_EPOCH = datetime(1904, 1, 1)


def image_bytes(
    fmt: str = "JPEG",
    color: tuple[int, int, int] = (200, 30, 30),
    size: tuple[int, int] = (64, 48),
    exif_date: str | None = None,
    orientation: int | None = None,
) -> bytes:
    """Encode a solid-colour image; optional EXIF DateTimeOriginal / Orientation."""
    img = Image.new("RGB", size, color)
    kwargs: dict[str, bytes] = {}
    if exif_date or orientation:
        exif = Image.Exif()
        if exif_date:
            exif.get_ifd(EXIF_IFD)[DATETIME_ORIGINAL] = exif_date
        if orientation:
            exif[ORIENTATION] = orientation
        kwargs["exif"] = exif.tobytes()
    buf = BytesIO()
    img.save(buf, fmt, **kwargs)
    return buf.getvalue()


def mp4_bytes(brand: bytes = b"isom") -> bytes:
    """Minimal bytes with an ISO-BMFF `ftyp` box header — enough for type sniffing."""
    return b"\x00\x00\x00\x18ftyp" + brand + b"\x00" * 100


def photo_bytes(
    seed: int,
    size: tuple[int, int] = (320, 240),
    fmt: str = "JPEG",
    exif_date: str | None = None,
) -> bytes:
    """Encode a smooth random 'photo'. Same seed → same picture at any size, so a resized
    copy is perceptually similar; different seeds are perceptually unrelated.
    (Solid-colour images all share one pHash, so they can't test near-duplicates.)"""
    rng = np.random.default_rng(seed)
    img = Image.fromarray(rng.integers(0, 256, (6, 8, 3), dtype=np.uint8)).resize(
        size, Image.Resampling.BICUBIC
    )
    kwargs: dict[str, bytes] = {}
    if exif_date:
        exif = Image.Exif()
        exif.get_ifd(EXIF_IFD)[DATETIME_ORIGINAL] = exif_date
        kwargs["exif"] = exif.tobytes()
    buf = BytesIO()
    img.save(buf, fmt, **kwargs)
    return buf.getvalue()


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def mp4_header_bytes(
    created: datetime | None, duration_s: float, version: int = 0, moov_last: bool = False
) -> bytes:
    """ftyp + moov/mvhd (+ an mdat): enough for the header reader, not playable."""
    secs = int((created - MAC_EPOCH).total_seconds()) if created else 0
    timescale = 1000
    dur = int(duration_s * timescale)
    if version == 0:
        body = struct.pack(">IIII", secs, secs, timescale, dur)
    else:
        body = struct.pack(">QQIQ", secs, secs, timescale, dur)
    mvhd = _box(b"mvhd", bytes([version, 0, 0, 0]) + body + b"\0" * 80)
    moov = _box(b"moov", mvhd)
    ftyp = _box(b"ftyp", b"isom\0\0\0\0isommp41")
    mdat = _box(b"mdat", b"\0" * 64)
    return ftyp + (mdat + moov if moov_last else moov + mdat)


def video_bytes(tmp: Path, seconds: float = 2.0, size: tuple[int, int] = (160, 120)) -> bytes:
    """A real, playable MP4 made with OpenCV (no binary fixtures in the repo).
    Left half red, right half a grey that changes per frame."""
    import cv2
    import numpy as np

    path = tmp / "gen.mp4"
    fps = 10
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for i in range(int(seconds * fps)):
        frame = np.full((size[1], size[0], 3), (i * 10) % 255, np.uint8)
        frame[:, : size[0] // 2] = (0, 0, 255)  # BGR red
        out.write(frame)
    out.release()
    data = path.read_bytes()
    path.unlink()
    return data
