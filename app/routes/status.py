from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy.orm import Session

import app as app_pkg
from app.deps import SessionDep
from app.services.analysis import status_counts
from app.services.face_worker import face_counts, scanning_enabled

router = APIRouter(prefix="/api")
health_router = APIRouter()


def faces_status(request: Request, session: Session) -> dict[str, Any]:
    state = request.app.state
    return {
        **face_counts(session),
        "model_installed": state.face_engine.available(),
        "enabled": scanning_enabled(session, state.settings),
        "error": state.face_worker.error,
    }


@router.get("/status")
def status(request: Request, session: SessionDep) -> dict[str, Any]:
    return {
        **status_counts(session),
        "model": request.app.state.embedder.info(),
        "error": request.app.state.worker.error,
        "faces": faces_status(request, session),
    }


@health_router.get("/healthz")
def healthz(request: Request) -> dict[str, Any]:
    """Identity probe for the launcher; answers even before first-run setup."""
    return {
        "app": "ogphotos",
        "version": app_pkg.__version__,
        "needs_setup": request.app.state.needs_setup,
    }
