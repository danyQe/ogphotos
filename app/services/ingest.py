import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import BinaryIO
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import File
from app.services.hashing import phash_hex, sha256_copy
from app.services.metadata import UnsupportedMedia, probe, resolve_taken_at
from app.services.phash_index import PhashIndex
from app.services.sidecar import parse_sidecar
from app.services.thumbnails import make_thumbnail, make_video_thumbnail
from app.services.tunables import get_int

log = logging.getLogger(__name__)

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SAFE_EXT = re.compile(r"\.[a-z0-9]{1,8}")
MAX_STEM = 100
# taken_at sources that are only a guess; a Takeout sidecar date beats them
GUESSED_SOURCES = {"mtime", "upload"}


@dataclass
class IngestResult:
    filename: str
    status: str  # "new" | "exact_duplicate" | "similar_exists" | "error"
    file_id: int | None = None
    existing_file_id: int | None = None
    existing_is_trashed: bool = False
    similar_file_ids: list[int] = field(default_factory=list)
    message: str = ""
    date_fixed: bool = False  # exact duplicate whose stored copy got its sidecar date


def basename(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/")).name


def library_relpath(original_name: str, sha256: str, year: int) -> str:
    p = PurePosixPath(original_name)
    ext = p.suffix.lower() if _SAFE_EXT.fullmatch(p.suffix.lower()) else ""
    stem = _UNSAFE_CHARS.sub("_", p.stem if ext else p.name).strip(" .")[:MAX_STEM] or "file"
    return f"library/{year}/{stem}__{sha256[:8]}{ext}"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _find_by_sha(session: Session, sha256: str) -> File | None:
    return session.scalar(select(File).where(File.sha256 == sha256))


def _redate(session: Session, existing: File, sidecar: bytes | None) -> bool:
    """Give a stored copy that only had a file-time date its Takeout sidecar date.

    Earlier uploads may have arrived without their .json; re-importing fixes the date.
    Only the DB row changes; the stored file stays where it is."""
    if existing.taken_at_source not in GUESSED_SOURCES or not sidecar:
        return False
    taken = parse_sidecar(sidecar)
    if taken is None:
        return False
    existing.taken_at, existing.taken_at_source = taken, "sidecar"
    session.commit()
    return True


def _duplicate(
    session: Session, filename: str, existing: File, sidecar: bytes | None = None
) -> IngestResult:
    fixed = _redate(session, existing, sidecar)
    year = existing.taken_at.year if existing.taken_at else "?"
    message = f"Exact copy of {existing.original_name} ({year})"
    if fixed:
        message += " — date updated from sidecar"
    if existing.is_trashed:
        message += " — currently in Trash"
    return IngestResult(
        filename,
        "exact_duplicate",
        existing_file_id=existing.id,
        existing_is_trashed=existing.is_trashed,
        message=message,
        date_fixed=fixed,
    )


def ingest_file(
    session: Session,
    settings: Settings,
    src: BinaryIO,
    filename: str,
    sidecar: bytes | None = None,
    mtime: datetime | None = None,
    phash_index: PhashIndex | None = None,
) -> IngestResult:
    """Hash, dedup, validate, store and record one uploaded file. Commits on success.

    Near-duplicates (pHash within the threshold of an indexed file) are still stored,
    reported as `similar_exists`. Without `phash_index` the near check is skipped."""
    name = basename(filename)
    if not name:
        return IngestResult(filename, "error", message="missing filename")

    tmp = settings.tmp_dir / f"{uuid4().hex}.part"  # our own temp copy; safe to unlink
    thumb = None
    try:
        sha, size = sha256_copy(src, tmp)
        if existing := _find_by_sha(session, sha):
            return _duplicate(session, filename, existing, sidecar)

        try:
            info = probe(tmp, name, settings.max_image_pixels)
        except UnsupportedMedia as e:
            return IngestResult(filename, "error", message=str(e))

        phash = phash_hex(tmp) if info.media_type == "image" else None
        similar: list[int] = []
        if phash and phash_index is not None:
            max_distance = get_int(session, "phash_max_distance", settings.phash_max_distance)
            matches = phash_index.search(phash, max_distance, settings.similar_max_matches)
            similar = [file_id for file_id, _ in matches]

        now = _utcnow()
        sidecar_dt = parse_sidecar(sidecar) if sidecar else None
        taken_at, source = resolve_taken_at(
            info.exif_taken_at,
            sidecar_dt,
            mtime,
            now,
            "metadata" if info.media_type == "video" else "exif",
        )
        rel = library_relpath(name, sha, taken_at.year)
        if (settings.data_dir / rel).exists():  # 8-char sha prefix collision
            rel = rel.replace(f"__{sha[:8]}", f"__{sha}", 1)
        dest = settings.data_dir / rel

        row = File(
            sha256=sha,
            phash=phash,
            original_name=name,
            stored_path=rel,
            media_type=info.media_type,
            mime=info.mime,
            size_bytes=size,
            width=info.width,
            height=info.height,
            duration_s=info.duration_s,
            taken_at=taken_at,
            taken_at_source=source,
            uploaded_at=now,
            analysis_status="pending" if info.media_type == "image" else "skipped",
            face_status="pending" if info.media_type == "image" else "skipped",
            is_trashed=False,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError:  # same sha inserted concurrently
            session.rollback()
            if existing := _find_by_sha(session, sha):
                return _duplicate(session, filename, existing, sidecar)
            raise

        thumb = settings.thumbs_dir / f"{row.id}.jpg"
        if info.media_type == "image":
            make_thumbnail(tmp, thumb, settings.thumb_size)
        elif not make_video_thumbnail(tmp, thumb, settings.thumb_size):
            thumb = None  # the tile falls back to "▶ name"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.replace(dest)
        session.commit()
        if phash and phash_index is not None:
            phash_index.add(row.id, phash)
        message = f"Stored in {taken_at.year}"
        if similar:
            n = len(similar)
            message += f" — similar to {n} photo{'s' if n > 1 else ''} already in library"
            return IngestResult(
                filename,
                "similar_exists",
                file_id=row.id,
                similar_file_ids=similar,
                message=message,
            )
        return IngestResult(filename, "new", file_id=row.id, message=message)
    except Exception as e:
        session.rollback()
        if thumb is not None:
            thumb.unlink(missing_ok=True)  # thumbnail we just created for a row that never landed
        log.exception("ingest failed for %s", filename)
        return IngestResult(filename, "error", message=f"ingest failed: {e}")
    finally:
        tmp.unlink(missing_ok=True)
