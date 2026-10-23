from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import FaceIndexDep, PeopleDep, ReclusterDep, SessionDep, SettingsDep
from app.models import File, Person
from app.routes.pages import templates
from app.routes.status import faces_status
from app.services.face_cluster import person_label
from app.services.face_worker import retry_failed, set_scanning
from app.services.library import file_json
from app.services.people import (
    cover_face_id,
    face_json,
    file_faces,
    list_people,
    person_faces,
    person_json,
    person_photos,
    unsorted_faces,
)

router = APIRouter()
PageNo = Annotated[int, Query(ge=1)]
PER_PAGE = 120


class PersonPatch(BaseModel):
    name: str | None = None
    hidden: bool | None = None
    cover_face_id: int | None = None


class MergeBody(BaseModel):
    into_person_id: int


class AssignBody(BaseModel):
    face_ids: list[int]
    person_id: int | None = None
    new: bool = False


class ScanningBody(BaseModel):
    enabled: bool


def _get_person(session: Session, person_id: int) -> Person:
    p = session.get(Person, person_id)
    if p is None:
        raise HTTPException(404, "person not found")
    return p


def _run(fn, *args):  # noqa: ANN001, ANN202 — map service errors to HTTP
    try:
        return fn(*args)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/api/people")
def api_people(session: SessionDep, hidden: bool = False) -> dict[str, Any]:
    return {"people": [person_json(p, c) for p, c in list_people(session, hidden)]}


@router.get("/api/people/{person_id}")
def api_person(person_id: int, session: SessionDep, page: PageNo = 1) -> dict[str, Any]:
    p = _get_person(session, person_id)
    photos = person_photos(session, p.id, page, PER_PAGE)
    return {
        **person_json(p, cover_face_id(session, p)),
        "total": photos.total,
        "page": photos.page,
        "per_page": photos.per_page,
        "photos": [file_json(f) for f in photos.files],
    }


@router.get("/api/people/{person_id}/faces")
def api_person_faces(person_id: int, session: SessionDep, page: PageNo = 1) -> dict[str, Any]:
    _get_person(session, person_id)
    faces, total = person_faces(session, person_id, page, PER_PAGE)
    return {"total": total, "faces": [face_json(f) for f in faces]}


@router.patch("/api/people/{person_id}")
def api_patch_person(
    person_id: int, body: PersonPatch, session: SessionDep, people: PeopleDep
) -> dict[str, Any]:
    _get_person(session, person_id)
    same: list[int] = []
    fields = body.model_fields_set
    if "name" in fields:
        same = _run(people.rename, session, person_id, body.name or "")
    if "hidden" in fields and body.hidden is not None:
        _run(people.set_hidden, session, person_id, body.hidden)
    if "cover_face_id" in fields and body.cover_face_id is not None:
        _run(people.set_cover, session, person_id, body.cover_face_id)
    p = _get_person(session, person_id)
    return {**person_json(p, cover_face_id(session, p)), "same_name": same}


@router.post("/api/people/{person_id}/merge")
def api_merge(
    person_id: int, body: MergeBody, session: SessionDep, people: PeopleDep
) -> dict[str, Any]:
    dst = _run(people.merge, session, person_id, body.into_person_id)
    return person_json(dst, cover_face_id(session, dst))


@router.get("/api/faces/unsorted")
def api_unsorted(session: SessionDep, index: FaceIndexDep, page: PageNo = 1) -> dict[str, Any]:
    faces, total = unsorted_faces(session, index, page, PER_PAGE)
    return {"total": total, "faces": [face_json(f) for f in faces]}


@router.get("/api/faces/{face_id}/crop")
def api_crop(face_id: int, settings: SettingsDep) -> FileResponse:
    path = settings.faces_dir / f"{face_id}.jpg"
    if not path.is_file():
        raise HTTPException(404, "face not found")
    return FileResponse(path, media_type="image/jpeg")


@router.post("/api/faces/{face_id}/remove")
def api_remove_face(face_id: int, session: SessionDep, people: PeopleDep) -> dict[str, Any]:
    new = _run(people.remove_face, session, face_id)
    return {"face_id": face_id, "person_id": new}


@router.post("/api/faces/assign")
def api_assign(body: AssignBody, session: SessionDep, people: PeopleDep) -> dict[str, Any]:
    if not body.new and body.person_id is None:
        raise HTTPException(400, "give person_id or new: true")
    pid = _run(people.assign_faces, session, body.face_ids, None if body.new else body.person_id)
    return {"person_id": pid}


@router.get("/api/files/{file_id}/faces")
def api_file_faces(file_id: int, session: SessionDep) -> dict[str, Any]:
    if session.get(File, file_id) is None:
        raise HTTPException(404, "file not found")
    return {
        "faces": [
            {
                **face_json(f),
                "label": person_label(p) if p else None,
                "hidden": bool(p and p.hidden),
            }
            for f, p in file_faces(session, file_id)
        ]
    }


@router.post("/api/faces/recluster")
def api_recluster(job: ReclusterDep) -> dict[str, Any]:
    if not job.start():
        raise HTTPException(409, "a recluster is already running")
    return asdict(job.state)


@router.post("/api/faces/retry-failed")
def api_retry_failed(session: SessionDep) -> dict[str, int]:
    return {"queued": retry_failed(session)}


@router.put("/api/faces/scanning")
def api_scanning(body: ScanningBody, session: SessionDep) -> dict[str, bool]:
    set_scanning(session, body.enabled)
    return {"enabled": body.enabled}


@router.get("/people", response_class=HTMLResponse)
def people_page(request: Request, session: SessionDep, hidden: bool = False) -> HTMLResponse:
    _, unsorted_total = unsorted_faces(session, request.app.state.face_index, 1, 1)
    return templates.TemplateResponse(
        request,
        "people.html",
        {
            "active": "people",
            "people": list_people(session, hidden),
            "show_hidden": hidden,
            "unsorted": unsorted_total,
            "faces_state": faces_status(request, session),
        },
    )


@router.get("/people/unsorted", response_class=HTMLResponse)
def unsorted_page(
    request: Request, session: SessionDep, index: FaceIndexDep, page: PageNo = 1
) -> HTMLResponse:
    faces, total = unsorted_faces(session, index, page, PER_PAGE)
    return templates.TemplateResponse(
        request,
        "people_unsorted.html",
        {
            "active": "people",
            "faces": faces,
            "total": total,
            "page": page,
            "has_more": page * PER_PAGE < total,
        },
    )


@router.get("/people/{person_id}", response_class=HTMLResponse)
def person_page(
    person_id: int, request: Request, session: SessionDep, view: str = "photos"
) -> HTMLResponse:
    p = _get_person(session, person_id)
    ctx: dict[str, Any] = {
        "active": "people",
        "person": p,
        "label": person_label(p),
        "cover": cover_face_id(session, p),
        "view": "faces" if view == "faces" else "photos",
    }
    if ctx["view"] == "faces":
        ctx["faces"], ctx["total"] = person_faces(session, p.id, 1, 1000)
    else:
        ctx["p"] = person_photos(session, p.id, 1, PER_PAGE)
    return templates.TemplateResponse(request, "person.html", ctx)


@router.get("/people/{person_id}/grid", response_class=HTMLResponse)
def person_grid(
    person_id: int, request: Request, session: SessionDep, page: PageNo = 1
) -> HTMLResponse:
    p = _get_person(session, person_id)
    return templates.TemplateResponse(
        request,
        "_person_grid.html",
        {"person": p, "p": person_photos(session, p.id, page, PER_PAGE)},
    )
