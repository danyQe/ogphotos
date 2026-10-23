from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import SessionDep, TrashDep, WorkerDep
from app.models import File
from app.routes.pages import templates

router = APIRouter()


def _trashed(session: Session) -> list[File]:
    return list(
        session.scalars(
            select(File).where(File.is_trashed.is_(True)).order_by(File.trashed_at.desc(), File.id)
        )
    )


@router.get("/api/trash")
def api_trash(session: SessionDep) -> dict[str, Any]:
    rows = _trashed(session)
    return {
        "total": len(rows),
        "size_bytes": sum(r.size_bytes for r in rows),
        "files": [
            {
                "file_id": r.id,
                "original_name": r.original_name,
                "size_bytes": r.size_bytes,
                "taken_at": r.taken_at,
                "trashed_at": r.trashed_at,
            }
            for r in rows
        ],
    }


@router.post("/api/trash/restore-all")
def api_restore_all(session: SessionDep, trash: TrashDep, worker: WorkerDep) -> dict[str, Any]:
    ids = list(session.scalars(select(File.id).where(File.is_trashed.is_(True))))
    restored = trash.restore(session, ids)
    for i in restored.needs_analysis:
        worker.enqueue(i)
    return {"restored": len(restored.file_ids)}


@router.post("/api/trash/empty")
def api_empty(session: SessionDep, trash: TrashDep, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        n = session.scalar(select(func.count()).where(File.is_trashed.is_(True)))
        raise HTTPException(400, f"pass confirm=true to permanently delete {n} file(s)")
    return {"deleted": trash.empty(session)}


@router.get("/trash", response_class=HTMLResponse)
def trash_page(request: Request, session: SessionDep) -> HTMLResponse:
    rows = _trashed(session)
    return templates.TemplateResponse(
        request,
        "trash.html",
        {"active": "trash", "files": rows, "size_bytes": sum(r.size_bytes for r in rows)},
    )
