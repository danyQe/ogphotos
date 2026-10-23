from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel

from app.routes.pages import templates
from app.services import locations
from app.services.folder_picker import PickerUnavailable, pick_folder

router = APIRouter()


class PathBody(BaseModel):
    path: str = ""


class BrowseBody(PathBody):
    title: str = ""


class SetupBody(BaseModel):
    path: str
    move_existing: bool = False  # move the old ./data library to `path` instead of copying


def _legacy() -> dict[str, Any] | None:
    """The pre-setup ./data library, if there is one worth keeping."""
    n = locations.photo_count(locations.LEGACY_DATA_DIR)
    if n is None:
        return None
    return {"path": str(locations.LEGACY_DATA_DIR), "photos": n}


def _info(request: Request) -> dict[str, Any]:
    return {
        "needs_setup": request.app.state.needs_setup,
        "default_path": str(locations.default_data_dir()),
        "config_file": str(locations.config_path()),
        "legacy": _legacy(),
    }


def _choice_json(choice: locations.Choice) -> dict[str, Any]:
    return {"path": str(choice.path), "exists": choice.exists, "photos": choice.photos}


@router.get("/api/setup")
def api_setup_info(request: Request) -> dict[str, Any]:
    return _info(request)


@router.post("/api/setup/check")
def api_setup_check(body: PathBody) -> dict[str, Any]:
    try:
        return _choice_json(locations.check_choice(body.path))
    except locations.LocationError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/api/browse-folder")
async def api_browse_folder(body: BrowseBody) -> dict[str, Any]:
    """Open the native folder dialog on this machine; `path: null` if cancelled."""
    initial = locations.pictures_dir()
    if body.path.strip():
        initial = locations.nearest_existing(Path(body.path.strip()))
    try:
        chosen = await run_in_threadpool(pick_folder, initial, body.title or "Choose a folder")
    except PickerUnavailable as e:
        raise HTTPException(501, str(e)) from e
    return {"path": str(chosen) if chosen else None}


@router.post("/api/setup")
def api_setup(body: SetupBody, request: Request) -> dict[str, Any]:
    app = request.app
    if not app.state.needs_setup:
        raise HTTPException(409, "the library folder is already set")
    try:
        choice = locations.check_choice(body.path)
        if body.move_existing:
            if _legacy() is None:
                raise locations.LocationError("there is no existing library to move")
            locations.move_library(locations.LEGACY_DATA_DIR, choice.path)
            choice = locations.check_choice(str(choice.path))
        choice.path.mkdir(parents=True, exist_ok=True)
    except locations.LocationError as e:
        raise HTTPException(400, str(e)) from e
    except OSError as e:
        raise HTTPException(400, f"can't use that folder: {e}") from e
    locations.save_config(choice.path)
    app.state.settings.data_dir = choice.path
    app.state.start_services()
    return {"data_dir": str(choice.path), "photos": choice.photos or 0}


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request) -> Response:
    if not request.app.state.needs_setup:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "setup.html", {"active": "setup", "setup": True, **_info(request)}
    )
