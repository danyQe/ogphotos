"""DNS rebinding: only loopback Host headers are served."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import allowed_hosts, create_app
from tests.fakes import FakeEmbedder, FakeFaceEngine


def test_evil_host_refused_on_get(client: TestClient) -> None:
    r = client.get("/api/status", headers={"host": "evil.example:8000"})
    assert r.status_code == 400


def test_evil_host_refused_on_post(client: TestClient) -> None:
    r = client.post(
        "/api/setup/models/embeddinggemma-2/import",
        json={"zip_path": "x.zip"},
        headers={"host": "evil.example:8000", "origin": "http://evil.example:8000"},
    )
    assert r.status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "[::1]:8000"])
def test_loopback_hosts_served(client: TestClient, host: str) -> None:
    assert client.get("/api/status", headers={"host": host}).status_code == 200


def test_testserver_not_allowed_by_default(settings: Settings) -> None:
    app = create_app(settings, FakeEmbedder(settings.embed_dim), face_engine=FakeFaceEngine())
    with TestClient(app) as c:  # TestClient's default Host is "testserver"
        assert c.get("/healthz").status_code == 400


def test_allowed_hosts_adds_specific_host_not_wildcard(tmp_path: Path) -> None:
    base = {"127.0.0.1", "localhost", "[::1]", "::1"}
    assert set(allowed_hosts(Settings(data_dir=tmp_path))) == base
    assert set(allowed_hosts(Settings(data_dir=tmp_path, host="0.0.0.0"))) == base
    assert set(allowed_hosts(Settings(data_dir=tmp_path, host="::"))) == base
    assert "photos.lan" in allowed_hosts(Settings(data_dir=tmp_path, host="photos.lan"))
