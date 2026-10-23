"""Model setup: list, download (after the user accepts the license), cancel, import a zip."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from app.models_registry import EMBED_MODEL_ID, REGISTRY, ModelSpec
from app.routes.pages import templates
from app.services.model_install import InstallError, ModelInstaller, is_installed

router = APIRouter()


class InstallBody(BaseModel):
    accept_license: bool = False


class ImportBody(BaseModel):
    zip_path: str


def models_missing(request: Request) -> bool:
    """Show the 'set up models' banner: required model absent and not on a setup page."""
    if request.url.path.startswith("/setup"):
        return False
    return not is_installed(request.app.state.settings, REGISTRY[EMBED_MODEL_ID])


templates.env.globals["models_missing"] = models_missing


def _installer(request: Request) -> ModelInstaller:
    return request.app.state.model_installer


def _spec(model_id: str) -> ModelSpec:
    spec = REGISTRY.get(model_id)
    if spec is None:
        raise HTTPException(404, f"unknown model {model_id}")
    return spec


def _require_folder(request: Request) -> None:
    if request.app.state.needs_setup:
        raise HTTPException(409, "choose a library folder at /setup first")


def _entry(spec: ModelSpec, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": spec.id,
        "name": spec.name,
        "purpose": spec.purpose,
        "required": spec.required,
        "size": spec.total_size,
        "license_name": spec.license_name,
        "license_url": spec.license_url,
        "license_summary": spec.license_summary,
        **state,
    }


def _one(request: Request, spec: ModelSpec) -> dict[str, Any]:
    return _entry(spec, _installer(request).state()[spec.id])


@router.get("/api/setup/models")
def api_models(request: Request) -> list[dict[str, Any]]:
    state = _installer(request).state()
    return [_entry(spec, state[spec.id]) for spec in REGISTRY.values()]


@router.post("/api/setup/models/{model_id}/install", status_code=202)
def api_install(model_id: str, body: InstallBody, request: Request) -> Response:
    spec = _spec(model_id)
    if not body.accept_license:
        raise HTTPException(400, f"accept the {spec.license_name} to download {spec.name}")
    _require_folder(request)
    try:
        _installer(request).start(model_id)
    except InstallError as e:
        raise HTTPException(409, str(e)) from e
    return JSONResponse(_one(request, spec), status_code=202)


@router.post("/api/setup/models/{model_id}/cancel")
def api_cancel(model_id: str, request: Request) -> dict[str, Any]:
    spec = _spec(model_id)
    _installer(request).cancel(model_id)
    return _one(request, spec)


@router.post("/api/setup/models/{model_id}/import")
def api_import(model_id: str, body: ImportBody, request: Request) -> dict[str, Any]:
    """Install from a zip the user already downloaded (runs in the threadpool)."""
    spec = _spec(model_id)
    _require_folder(request)
    path = Path(body.zip_path.strip().strip('"').strip("'"))  # "Copy as path" adds quotes
    try:
        _installer(request).import_zip(model_id, path)
    except InstallError as e:
        raise HTTPException(400, str(e)) from e
    return _one(request, spec)


@router.get("/setup/models", response_class=HTMLResponse)
def setup_models_page(request: Request) -> Response:
    """Works both before the library folder is chosen and after."""
    setup = request.app.state.needs_setup
    return templates.TemplateResponse(
        request, "setup_models.html", {"active": "settings", "setup": setup}
    )
