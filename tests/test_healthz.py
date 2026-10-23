from fastapi.testclient import TestClient

import app as app_pkg
from app.config import Settings
from app.main import create_app
from tests.fakes import BASE_URL, FakeEmbedder, FakeFaceEngine


def test_healthz_returns_identity(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"app": "ogphotos", "version": app_pkg.__version__, "needs_setup": False}


def test_healthz_works_in_setup_mode(settings: Settings) -> None:
    app = create_app(
        settings, FakeEmbedder(settings.embed_dim), needs_setup=True, face_engine=FakeFaceEngine()
    )
    with TestClient(app, base_url=BASE_URL) as c:
        r = c.get("/healthz", follow_redirects=False)
        assert r.status_code == 200
        assert r.json()["needs_setup"] is True
