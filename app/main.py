import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import Settings
from app.db import init_db
from app.models_registry import EMBED_MODEL_ID
from app.routes import (
    files,
    groups,
    importer,
    library,
    model_setup,
    pages,
    people,
    setup,
    status,
    trash,
    upload,
)
from app.routes import settings as settings_routes
from app.services.analysis import AnalysisWorker
from app.services.embedder import Embedder, GemmaEmbedder
from app.services.face_cluster import PeopleClusterer, ReclusterJob
from app.services.face_engine import FaceEngine, InsightFaceEngine
from app.services.face_index import FaceIndex
from app.services.face_worker import FaceWorker
from app.services.grouping import Grouper
from app.services.importer import Importer
from app.services.locations import resolve_settings
from app.services.model_install import ModelInstaller
from app.services.people import People
from app.services.phash_backfill import backfill_phashes
from app.services.phash_index import PhashIndex
from app.services.regroup import Regrouper
from app.services.trash import Trash
from app.services.vector_index import VectorIndex
from app.services.video_backfill import backfill_videos

APP_DIR = Path(__file__).parent

# Reachable before a library folder is chosen
SETUP_PATHS = ("/setup", "/api/setup", "/api/browse-folder", "/healthz", "/static/")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")
WILDCARD_HOSTS = {"0.0.0.0", "::", ""}


def allowed_hosts(settings: Settings) -> list[str]:
    """Host headers we answer. Anything else (e.g. a DNS-rebinding site whose name resolves
    to 127.0.0.1) gets 400 before any route runs."""
    hosts = list(LOOPBACK_HOSTS)
    if settings.host not in WILDCARD_HOSTS and settings.host not in hosts:
        hosts.append(settings.host)
    return hosts


def _start_services(app: FastAPI) -> None:
    """Open the data folder and start indexes and background threads."""
    settings: Settings = app.state.settings
    engine, factory = init_db(settings)
    app.state.engine = engine
    app.state.session_factory = factory
    with factory() as session:
        app.state.phash_index = PhashIndex.load(session)
        app.state.vector_index = VectorIndex.load(session, settings.embed_dim)
        app.state.face_index = FaceIndex.load(session)
    stop = threading.Event()
    backfill = threading.Thread(
        target=backfill_phashes,
        args=(factory, settings, app.state.phash_index, stop),
        name="phash-backfill",
        daemon=True,
    )
    backfill.start()
    videos = threading.Thread(
        target=backfill_videos, args=(factory, settings, stop), name="video-backfill", daemon=True
    )
    videos.start()
    grouper = Grouper(settings, app.state.vector_index, app.state.phash_index)
    app.state.clusterer = PeopleClusterer(settings, app.state.face_index)
    app.state.face_worker = FaceWorker(
        factory, settings, app.state.face_engine, app.state.clusterer
    )
    app.state.face_worker.start()
    app.state.people = People(app.state.clusterer)
    app.state.recluster_job = ReclusterJob(factory, app.state.clusterer)
    app.state.trash = Trash(settings, grouper, app.state.clusterer)
    app.state.worker = AnalysisWorker(
        factory, settings, app.state.embedder, app.state.vector_index, grouper
    )
    app.state.worker.start()
    app.state.regrouper = Regrouper(factory, grouper)
    app.state.importer = Importer(
        factory, settings, app.state.phash_index, app.state.worker.enqueue
    )

    def stop_services() -> None:
        app.state.importer.stop()
        app.state.regrouper.wait()
        app.state.worker.stop()
        app.state.recluster_job.wait()
        app.state.face_worker.stop()
        stop.set()
        backfill.join()
        videos.join()
        engine.dispose()

    app.state.stop_services = stop_services
    app.state.needs_setup = False


def create_app(
    settings: Settings | None = None,
    embedder: Embedder | None = None,
    needs_setup: bool | None = None,
    face_engine: FaceEngine | None = None,
) -> FastAPI:
    """`settings` defaults to env + the per-user config file; with no folder chosen yet the
    app starts in setup mode (only /setup works). `embedder` defaults to EmbeddingGemma 2
    (loaded lazily); tests pass a fake.

    There is no module-level app (importing this module must not read or migrate any
    config). Dev server: `uv run uvicorn app.main:create_app --factory --reload`."""
    if settings is None:
        settings, resolved_setup = resolve_settings()
        needs_setup = resolved_setup if needs_setup is None else needs_setup
    embedder = embedder or GemmaEmbedder(settings)
    face_engine = face_engine or InsightFaceEngine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not app.state.needs_setup:
            try:
                _start_services(app)
            except Exception as e:  # uvicorn only exits; keep the reason for the launcher
                app.state.startup_error = e
                raise
        yield
        app.state.model_installer.stop()
        if app.state.stop_services is not None:
            app.state.stop_services()

    app = FastAPI(title="OGphotos", lifespan=lifespan)
    app.state.settings = settings
    app.state.embedder = embedder
    app.state.face_engine = face_engine
    app.state.needs_setup = bool(needs_setup)
    app.state.stop_services = None
    app.state.startup_error = None
    app.state.start_services = lambda: _start_services(app)

    def on_installed(model_id: str) -> None:
        # Files left `pending` while the model was missing get analysed now, no restart.
        if model_id == EMBED_MODEL_ID and app.state.stop_services is not None:
            app.state.worker.resume_pending()

    app.state.model_installer = ModelInstaller(settings, on_installed)

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Other websites can send requests to localhost; refuse cross-site writes.
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        if app.state.needs_setup and not request.url.path.startswith(SETUP_PATHS):
            if request.url.path.startswith("/api/"):
                return JSONResponse(
                    {"detail": "choose a library folder at /setup first"}, status_code=503
                )
            return RedirectResponse("/setup", status_code=302)
        return await call_next(request)

    # Added last, so it runs first (outermost).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts(settings))
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    app.include_router(setup.router)
    app.include_router(model_setup.router)
    app.include_router(pages.router)
    app.include_router(upload.router)
    app.include_router(files.router)
    app.include_router(status.router)
    app.include_router(status.health_router)
    app.include_router(groups.router)
    app.include_router(trash.router)
    app.include_router(library.router)
    app.include_router(settings_routes.router)
    app.include_router(importer.router)
    app.include_router(people.router)
    return app
