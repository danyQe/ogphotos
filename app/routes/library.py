from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.deps import SessionDep, SettingsDep, VectorIndexDep
from app.models import File
from app.routes.pages import templates
from app.services.library import (
    INLINE_MIMES,
    list_files,
    month_counts,
    similar_files,
    year_counts,
)
from app.services.people import file_faces

router = APIRouter()

PER_PAGE = 120
SIMILAR_LIMIT = 12
SIMILAR_MIN_COSINE = 0.0  # "show similar" lists the closest photos whatever the threshold

Month = Annotated[int | None, Query(ge=1, le=12)]
PageNo = Annotated[int, Query(ge=1)]


def _grid_context(
    session: Session, year: int | None, month: int | None, page: int, after: str
) -> dict[str, Any]:
    p = list_files(session, year, month if year else None, page=page, per_page=PER_PAGE)
    return {"p": p, "year": year, "month": month if year else None, "after": after}


@router.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request, session: SessionDep, year: int | None = None, month: Month = None
) -> HTMLResponse:
    ctx = _grid_context(session, year, month, 1, "")
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "active": "library",
            "years": year_counts(session),
            "months": month_counts(session, year) if year else [],
            **ctx,
        },
    )


@router.get("/library/grid", response_class=HTMLResponse)
def library_grid(
    request: Request,
    session: SessionDep,
    year: int | None = None,
    month: Month = None,
    page: PageNo = 1,
    after: str = "",
) -> HTMLResponse:
    """Next chunk of the grid (htmx infinite scroll). `after` = last month heading shown."""
    return templates.TemplateResponse(
        request, "_library_grid.html", _grid_context(session, year, month, page, after)
    )


@router.get("/files/{file_id}", response_class=HTMLResponse)
def file_page(
    file_id: int,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    vectors: VectorIndexDep,
) -> HTMLResponse:
    row = session.get(File, file_id)
    if row is None:
        raise HTTPException(404, "file not found")
    return templates.TemplateResponse(
        request,
        "file.html",
        {
            "active": "trash" if row.is_trashed else "library",
            "f": row,
            "inline": row.mime in INLINE_MIMES,
            "faces": [(f, p) for f, p in file_faces(session, file_id) if not (p and p.hidden)],
            "similar": similar_files(session, vectors, file_id, SIMILAR_MIN_COSINE, SIMILAR_LIMIT),
        },
    )
