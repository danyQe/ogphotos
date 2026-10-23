from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.deps import SessionDep, SettingsDep, TrashDep, VectorIndexDep, WorkerDep
from app.models import File
from app.services.hashing import hamming
from app.services.library import file_json, list_files
from app.services.tunables import get_float

router = APIRouter(prefix="/api/files")


@router.get("")
def api_files(
    session: SessionDep,
    year: int | None = None,
    month: Annotated[int | None, Query(ge=1, le=12)] = None,
    trashed: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=500)] = 120,
) -> dict[str, Any]:
    """Date-sorted (newest first); `month` needs `year`."""
    p = list_files(session, year, month, trashed, page, per_page)
    return {
        "total": p.total,
        "page": p.page,
        "per_page": p.per_page,
        "files": [file_json(f) for f in p.files],
    }


@router.get("/{file_id}")
def api_file(file_id: int, session: SessionDep) -> dict[str, Any]:
    row = session.get(File, file_id)
    if row is None:
        raise HTTPException(404, "file not found")
    return file_json(row)


@router.get("/{file_id}/thumb")
def thumb(file_id: int, settings: SettingsDep) -> FileResponse:
    path = settings.thumbs_dir / f"{file_id}.jpg"
    if not path.is_file():
        raise HTTPException(404, "thumbnail not found")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/{file_id}/original")
def original(file_id: int, session: SessionDep, settings: SettingsDep) -> FileResponse:
    row = session.get(File, file_id)
    path = settings.data_dir / row.stored_path if row else None
    if path is None or not path.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(path, media_type=row.mime)


@router.get("/{file_id}/similar")
def similar(
    file_id: int,
    session: SessionDep,
    settings: SettingsDep,
    vectors: VectorIndexDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    min_cosine: Annotated[float | None, Query(ge=-1, le=1)] = None,
) -> dict[str, Any]:
    """Embedding neighbors of an analyzed file, most similar first."""
    row = session.get(File, file_id)
    if row is None:
        raise HTTPException(404, "file not found")
    vec = vectors.get(file_id)
    if vec is None:
        raise HTTPException(409, f"file not analyzed (status: {row.analysis_status})")
    if min_cosine is None:
        min_cosine = get_float(session, "embed_min_cosine", settings.embed_min_cosine)
    hits = vectors.search(vec, min_cosine, limit, exclude_id=file_id)
    others = {
        f.id: f for f in session.scalars(select(File).where(File.id.in_([i for i, _ in hits])))
    }
    return {
        "file_id": file_id,
        "min_cosine": min_cosine,
        "neighbors": [
            {
                "file_id": other_id,
                "cosine": round(cos, 4),
                "phash_distance": (
                    hamming(row.phash, others[other_id].phash)
                    if row.phash and others[other_id].phash
                    else None
                ),
                "original_name": others[other_id].original_name,
            }
            for other_id, cos in hits
        ],
    }


@router.post("/{file_id}/trash")
def trash_file(file_id: int, session: SessionDep, trash: TrashDep) -> dict[str, Any]:
    row = session.get(File, file_id)
    if row is None:
        raise HTTPException(404, "file not found")
    trash.trash(session, [file_id])
    return {"file_id": file_id, "is_trashed": True}


@router.post("/{file_id}/restore")
def restore_file(
    file_id: int, session: SessionDep, trash: TrashDep, worker: WorkerDep
) -> dict[str, Any]:
    row = session.get(File, file_id)
    if row is None:
        raise HTTPException(404, "file not found")
    restored = trash.restore(session, [file_id])
    for i in restored.needs_analysis:
        worker.enqueue(i)
    session.refresh(row)
    return {"file_id": file_id, "is_trashed": False, "group_id": row.group_id}
