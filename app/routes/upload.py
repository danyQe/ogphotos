from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.deps import PhashIndexDep, SessionDep, SettingsDep, WorkerDep
from app.services.ingest import ingest_file
from app.services.sidecar import find_sidecar

router = APIRouter(prefix="/api")

SIDECAR_MAX_BYTES = 1024 * 1024
ANALYZE_STATUSES = {"new", "similar_exists"}


def _ms_to_datetime(ms: int) -> datetime | None:
    if ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=UTC).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


@router.post("/upload")
def upload(
    session: SessionDep,
    settings: SettingsDep,
    phash_index: PhashIndexDep,
    worker: WorkerDep,
    files: Annotated[list[UploadFile], File()],
    paths: Annotated[list[str] | None, Form()] = None,
    mtimes: Annotated[list[int] | None, Form()] = None,
) -> dict[str, list[dict[str, Any]]]:
    for name, values in (("paths", paths), ("mtimes", mtimes)):
        if values is not None and len(values) != len(files):
            raise HTTPException(422, f"{name} must have one entry per file")

    entries = [
        (
            (paths[i] if paths and paths[i] else f.filename) or "",
            _ms_to_datetime(mtimes[i]) if mtimes else None,
            f,
        )
        for i, f in enumerate(files)
    ]
    sidecars = {
        path: f.file.read(SIDECAR_MAX_BYTES)
        for path, _, f in entries
        if path.lower().endswith(".json")
    }

    results = []
    for path, mtime, f in entries:
        if path.lower().endswith(".json"):
            continue
        result = ingest_file(
            session,
            settings,
            f.file,
            path,
            sidecar=find_sidecar(path, sidecars),
            mtime=mtime,
            phash_index=phash_index,
        )
        if result.status in ANALYZE_STATUSES and result.file_id is not None:
            worker.enqueue(result.file_id)  # worker skips videos (not pending)
        results.append(asdict(result))
    return {"results": results}
