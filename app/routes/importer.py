from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.deps import ImporterDep
from app.routes.pages import templates
from app.services.importer import ImportRejected

router = APIRouter()


class ImportRequest(BaseModel):
    path: str


@router.post("/api/import-folder")
def api_start_import(body: ImportRequest, importer: ImporterDep) -> dict[str, Any]:
    try:
        job = importer.start(body.path)
    except ImportRejected as e:
        raise HTTPException(400, str(e)) from e
    return job.snapshot()


@router.get("/api/import-folder/{job_id}")
def api_import_status(
    job_id: str, importer: ImporterDep, offset: Annotated[int, Query(ge=0)] = 0
) -> dict[str, Any]:
    """Progress; `results` from `offset` on, so the page fetches only new rows."""
    job = importer.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "import job not found")
    return job.snapshot(offset)


@router.post("/api/import-folder/{job_id}/cancel")
def api_cancel_import(job_id: str, importer: ImporterDep) -> dict[str, Any]:
    if job_id not in importer.jobs:
        raise HTTPException(404, "import job not found")
    importer.cancel(job_id)
    return {"id": job_id, "cancelling": True}


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request, importer: ImporterDep) -> HTMLResponse:
    running = next((j for j in importer.jobs.values() if j.status == "running"), None)
    return templates.TemplateResponse(
        request, "import.html", {"active": "import", "running": running}
    )
