from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import RegrouperDep, SessionDep, SettingsDep
from app.models import File, Group
from app.routes.pages import templates
from app.routes.status import faces_status
from app.services.analysis import status_counts
from app.services.locations import config_path
from app.services.regroup import Regrouper
from app.services.tunables import TUNABLES, current, update_tunables

router = APIRouter()


def _library_stats(session: Session) -> dict[str, int]:
    def count(*where: Any) -> int:
        return session.scalar(select(func.count(File.id)).where(*where)) or 0

    return {
        "files": count(File.is_trashed.is_(False)),
        "trashed": count(File.is_trashed.is_(True)),
        "groups": session.scalar(select(func.count(Group.id))) or 0,
    }


def _settings_json(
    request: Request, session: Session, settings: Settings, regrouper: Regrouper
) -> dict[str, Any]:
    return {
        "thresholds": current(session, settings.tunable_defaults()),
        "model": request.app.state.embedder.info(),
        "analysis": status_counts(session),
        "library": _library_stats(session),
        "regroup": asdict(regrouper.state),
        "data_dir": str(settings.data_dir.resolve()),
        "config_file": str(config_path()),
        "faces": {
            **faces_status(request, session),
            "model": request.app.state.face_engine.info(),
            "setup_url": "/setup/models",
            "recluster": asdict(request.app.state.recluster_job.state),
        },
    }


@router.get("/api/settings")
def api_settings(
    request: Request, session: SessionDep, settings: SettingsDep, regrouper: RegrouperDep
) -> dict[str, Any]:
    return _settings_json(request, session, settings, regrouper)


@router.put("/api/settings")
def api_update_settings(
    values: dict[str, Any],
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    regrouper: RegrouperDep,
) -> dict[str, Any]:
    """Change thresholds. New photos use them at once; existing groups after a regroup."""
    try:
        update_tunables(session, values)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _settings_json(request, session, settings, regrouper)


@router.post("/api/regroup")
def api_regroup(regrouper: RegrouperDep, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        raise HTTPException(400, "pass confirm=true: regrouping clears all reviewed flags")
    if not regrouper.start():
        raise HTTPException(409, "a regroup is already running")
    return asdict(regrouper.state)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request, session: SessionDep, settings: SettingsDep, regrouper: RegrouperDep
) -> HTMLResponse:
    data = _settings_json(request, session, settings, regrouper)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active": "settings",
            "similarity_tunables": [
                (t, data["thresholds"][t.key]) for t in TUNABLES if t.section == "similarity"
            ],
            "face_tunables": [
                (t, data["thresholds"][t.key]) for t in TUNABLES if t.section == "faces"
            ],
            "s": data,
        },
    )
