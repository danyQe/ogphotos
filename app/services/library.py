"""Read models for the Library page: date-sorted files with year/month filters."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Integer, Select, cast, func, select
from sqlalchemy.orm import Session

from app.models import File
from app.services.vector_index import VectorIndex

_YEAR = cast(func.strftime("%Y", File.taken_at), Integer)
_MONTH = cast(func.strftime("%m", File.taken_at), Integer)

# Browsers show these inline; anything else (HEIC, TIFF…) is shown via its thumbnail
INLINE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp"}


@dataclass
class Page:
    total: int
    page: int
    per_page: int
    files: list[File]

    @property
    def has_more(self) -> bool:
        return self.page * self.per_page < self.total


def _filtered(stmt: Select[Any], year: int | None, month: int | None, trashed: bool) -> Select[Any]:
    stmt = stmt.where(File.is_trashed.is_(trashed))
    if year is not None:
        stmt = stmt.where(_YEAR == year)
        if month is not None:
            stmt = stmt.where(_MONTH == month)
    return stmt


def list_files(
    session: Session,
    year: int | None = None,
    month: int | None = None,
    trashed: bool = False,
    page: int = 1,
    per_page: int = 120,
) -> Page:
    """Newest first. `month` only applies together with `year`."""
    total = session.scalar(_filtered(select(func.count(File.id)), year, month, trashed)) or 0
    rows = session.scalars(
        _filtered(select(File), year, month, trashed)
        .order_by(File.taken_at.desc(), File.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    return Page(total=total, page=page, per_page=per_page, files=list(rows))


def year_counts(session: Session) -> list[tuple[int, int]]:
    """[(year, live file count)] newest first."""
    rows = session.execute(
        _filtered(select(_YEAR, func.count(File.id)), None, None, False)
        .where(File.taken_at.is_not(None))
        .group_by(_YEAR)
        .order_by(_YEAR.desc())
    ).all()
    return [(int(y), int(n)) for y, n in rows]


def month_counts(session: Session, year: int) -> list[tuple[int, int]]:
    rows = session.execute(
        _filtered(select(_MONTH, func.count(File.id)), year, None, False)
        .group_by(_MONTH)
        .order_by(_MONTH.desc())
    ).all()
    return [(int(m), int(n)) for m, n in rows]


def file_json(f: File) -> dict[str, Any]:
    return {
        "file_id": f.id,
        "original_name": f.original_name,
        "media_type": f.media_type,
        "mime": f.mime,
        "size_bytes": f.size_bytes,
        "width": f.width,
        "height": f.height,
        "duration_s": f.duration_s,
        "taken_at": f.taken_at,
        "taken_at_source": f.taken_at_source,
        "uploaded_at": f.uploaded_at,
        "sharpness": f.sharpness,
        "analysis_status": f.analysis_status,
        "analysis_error": f.analysis_error,
        "group_id": f.group_id,
        "is_trashed": f.is_trashed,
        "trashed_at": f.trashed_at,
    }


def similar_files(
    session: Session, vectors: VectorIndex, file_id: int, min_cosine: float, limit: int
) -> list[tuple[File, float]]:
    """Embedding neighbours of a file (live ones only), most similar first."""
    vec = vectors.get(file_id)
    if vec is None:
        return []
    hits = vectors.search(vec, min_cosine, limit, exclude_id=file_id)
    rows = {
        f.id: f
        for f in session.scalars(
            select(File).where(File.id.in_([i for i, _ in hits]), File.is_trashed.is_(False))
        )
    }
    return [(rows[i], cos) for i, cos in hits if i in rows]
