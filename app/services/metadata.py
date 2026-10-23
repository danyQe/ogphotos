from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from PIL import Image, UnidentifiedImageError

from app.services.video_meta import read_mp4_header

IMAGE_MIMES = {
    "JPEG": "image/jpeg",
    "MPO": "image/jpeg",  # JPEG with MPF frames (HDR gain maps on modern phones)
    "PNG": "image/png",
    "HEIF": "image/heic",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}
VIDEO_EXTS = {".mp4", ".mov"}

EXIF_IFD = 0x8769
EXIF_DATETIME_ORIGINAL = 36867
EXIF_DATETIME = 306
EXIF_FORMAT = "%Y:%m:%d %H:%M:%S"


class UnsupportedMedia(ValueError):
    """File is not an accepted image/video; message is shown to the user."""


@dataclass(frozen=True)
class MediaInfo:
    media_type: str  # "image" | "video"
    mime: str
    width: int | None = None
    height: int | None = None
    exif_taken_at: datetime | None = None
    duration_s: float | None = None


def probe(path: Path, filename: str, max_pixels: int | None = None) -> MediaInfo:
    """Identify the file at `path` by content (and extension, for videos).

    Images over `max_pixels` are rejected from the header, before decoding."""
    if PurePosixPath(filename).suffix.lower() in VIDEO_EXTS:
        return _probe_video(path)
    try:
        with Image.open(path) as img:
            if img.format not in IMAGE_MIMES:
                raise UnsupportedMedia(f"unsupported image format: {img.format}")
            width, height = img.size
            if max_pixels is not None and width * height > max_pixels:
                raise UnsupportedMedia(
                    f"image of {width * height:,} pixels exceeds the {max_pixels:,} pixel "
                    "limit (raise OGPHOTOS_MAX_IMAGE_PIXELS)"
                )
            img.load()
            taken = _exif_taken_at(img)
            mime = IMAGE_MIMES[img.format]
    except UnsupportedMedia:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as e:
        raise UnsupportedMedia(f"not a readable image: {e}") from e
    return MediaInfo("image", mime, width, height, taken)


def _probe_video(path: Path) -> MediaInfo:
    with path.open("rb") as f:
        head = f.read(12)
    if len(head) < 12 or head[4:8] != b"ftyp":
        raise UnsupportedMedia("not a valid MP4/MOV file")
    mime = "video/quicktime" if head[8:12] == b"qt  " else "video/mp4"
    created, duration = read_mp4_header(path)
    return MediaInfo("video", mime, exif_taken_at=created, duration_s=duration)


def _exif_taken_at(img: Image.Image) -> datetime | None:
    exif = img.getexif()
    raw = exif.get_ifd(EXIF_IFD).get(EXIF_DATETIME_ORIGINAL) or exif.get(EXIF_DATETIME)
    return parse_exif_datetime(raw)


def parse_exif_datetime(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw.strip("\x00 ")[:19], EXIF_FORMAT)
    except ValueError:
        return None


def resolve_taken_at(
    exif: datetime | None,
    sidecar: datetime | None,
    mtime: datetime | None,
    now: datetime,
    first_source: str = "exif",
) -> tuple[datetime, str]:
    """Pick the best date: file's own date → Takeout sidecar → file mtime → upload time."""
    for value, source in ((exif, first_source), (sidecar, "sidecar"), (mtime, "mtime")):
        if value is not None:
            return value, source
    return now, "upload"
