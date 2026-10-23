from dataclasses import asdict
from math import ceil
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import SessionDep, SettingsDep, TrashDep
from app.models import Group
from app.routes.pages import templates
from app.services.groups import GroupDetail, group_detail, list_groups, prev_next
from app.services.review import (
    GroupNotFound,
    NotAMember,
    apply_selection,
    keep_best,
    mark_reviewed,
    split_out,
)

router = APIRouter()

PER_PAGE = 60
SHOW = {"all": None, "unreviewed": False, "reviewed": True}


def _detail_json(d: GroupDetail) -> dict[str, Any]:
    g = d.group
    return {
        "id": g.id,
        "size": g.size,
        "reviewed": g.reviewed,
        "best_file_id": g.best_file_id,
        "prev_id": d.prev_id,
        "next_id": d.next_id,
        "members": [
            {
                "file_id": m.file.id,
                "original_name": m.file.original_name,
                "width": m.file.width,
                "height": m.file.height,
                "size_bytes": m.file.size_bytes,
                "taken_at": m.file.taken_at,
                "sharpness": m.file.sharpness,
                "score": round(m.score, 4),
                "is_best": m.is_best,
            }
            for m in d.members
        ],
    }


@router.get("/api/groups")
def api_groups(
    session: SessionDep,
    reviewed: bool | None = None,
    year: int | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
) -> dict[str, Any]:
    groups = list_groups(session, reviewed, year)
    chunk = groups[(page - 1) * PER_PAGE : page * PER_PAGE]
    return {
        "total": len(groups),
        "page": page,
        "per_page": PER_PAGE,
        "groups": [asdict(g) for g in chunk],
    }


@router.get("/api/groups/{group_id}")
def api_group(group_id: int, session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    detail = group_detail(session, group_id, settings.best_weights())
    if detail is None:
        raise HTTPException(404, "group not found")
    return _detail_json(detail)


class Selection(BaseModel):
    keep: list[int] = []
    trash: list[int] = []


def _next_id(session: Session, group_id: int) -> int | None:
    """Where to go after acting on a group (computed before the group can dissolve)."""
    if session.get(Group, group_id) is None:
        raise HTTPException(404, "group not found")
    return prev_next(session, group_id)[1]


@router.post("/api/groups/{group_id}/keep-best")
def api_keep_best(group_id: int, session: SessionDep, trash: TrashDep) -> dict[str, Any]:
    next_id = _next_id(session, group_id)
    try:
        trashed = keep_best(session, trash, group_id)
    except GroupNotFound as e:
        raise HTTPException(404, str(e)) from e
    return {"trashed": trashed, "next_id": next_id}


@router.post("/api/groups/{group_id}/apply")
def api_apply(
    group_id: int, selection: Selection, session: SessionDep, trash: TrashDep
) -> dict[str, Any]:
    next_id = _next_id(session, group_id)
    try:
        trashed = apply_selection(session, trash, group_id, selection.keep, selection.trash)
    except GroupNotFound as e:
        raise HTTPException(404, str(e)) from e
    except NotAMember as e:
        raise HTTPException(400, str(e)) from e
    return {"trashed": trashed, "next_id": next_id}


@router.post("/api/groups/{group_id}/remove/{file_id}")
def api_split_out(
    group_id: int, file_id: int, session: SessionDep, trash: TrashDep
) -> dict[str, Any]:
    next_id = _next_id(session, group_id)
    try:
        new_gid = split_out(session, trash, group_id, file_id)
    except GroupNotFound as e:
        raise HTTPException(404, str(e)) from e
    except NotAMember as e:
        raise HTTPException(400, str(e)) from e
    return {
        "file_id": file_id,
        "new_group_id": new_gid,
        "group_exists": session.get(Group, group_id) is not None,
        "next_id": next_id,
    }


@router.post("/api/groups/{group_id}/reviewed")
def api_reviewed(group_id: int, session: SessionDep, reviewed: bool = True) -> dict[str, Any]:
    next_id = _next_id(session, group_id)
    mark_reviewed(session, group_id, reviewed)
    return {"id": group_id, "reviewed": reviewed, "next_id": next_id}


@router.get("/groups", response_class=HTMLResponse)
def groups_page(
    request: Request,
    session: SessionDep,
    show: str = "all",
    year: int | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
) -> HTMLResponse:
    show = show if show in SHOW else "all"
    every = list_groups(session)
    groups = list_groups(session, SHOW[show], year)
    return templates.TemplateResponse(
        request,
        "groups.html",
        {
            "active": "groups",
            "groups": groups[(page - 1) * PER_PAGE : page * PER_PAGE],
            "total": len(groups),
            "any_groups": bool(every),
            "page": page,
            "pages": max(1, ceil(len(groups) / PER_PAGE)),
            "show": show,
            "year": year,
            "years": sorted({g.date_from.year for g in every if g.date_from}, reverse=True),
        },
    )


@router.get("/groups/{group_id}", response_class=HTMLResponse)
def group_page(
    group_id: int, request: Request, session: SessionDep, settings: SettingsDep
) -> HTMLResponse:
    detail = group_detail(session, group_id, settings.best_weights())
    if detail is None:
        raise HTTPException(404, "group not found")
    return templates.TemplateResponse(request, "group.html", {"active": "groups", "d": detail})
