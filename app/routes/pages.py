from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app import __version__
from app.deps import SessionDep
from app.models import File
from app.services.face_cluster import person_label

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def filesize(n: int | None) -> str:
    size = float(n or 0)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


templates.env.filters["filesize"] = filesize
templates.env.globals["person_label"] = person_label
templates.env.globals["app_version"] = __version__


def duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = round(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


templates.env.filters["duration"] = duration

PAGES = ("upload",)  # other pages live in their own route modules


@router.get("/")
def index(session: SessionDep) -> RedirectResponse:
    has_files = session.scalar(select(File.id).limit(1)) is not None
    return RedirectResponse("/groups" if has_files else "/upload", status_code=302)


def _page(name: str) -> Callable[[Request], HTMLResponse]:
    def handler(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, f"{name}.html", {"active": name, "settings": request.app.state.settings}
        )

    return handler


for _name in PAGES:
    router.add_api_route(
        f"/{_name}", _page(_name), methods=["GET"], response_class=HTMLResponse, name=_name
    )
