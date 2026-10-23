import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models_registry import EMBED_MODEL_ID, FACE_MODEL_ID, REGISTRY, ModelSpec
from app.services import locations
from app.services.face_engine import InsightFaceEngine
from app.services.model_install import read_installed
from tests.fakes import BASE_URL, FakeEmbedder
from tests.httpfake import FakeServer
from tests.imgutil import photo_bytes
from tests.test_model_install import make_spec, make_zip

EMBED_FILES = {"config.json": b'{"model": "fake"}', "model.safetensors": bytes(range(256)) * 40}
FACE_FILES = {"det_10g.onnx": b"det" * 100, "w600k_r50.onnx": b"rec" * 100}


def _as(spec: ModelSpec, model_id: str, required: bool) -> ModelSpec:
    real = REGISTRY[model_id]
    return ModelSpec(
        id=model_id,
        name=real.name,
        purpose=real.purpose,
        required=required,
        license_name=real.license_name,
        license_url=real.license_url,
        license_summary=real.license_summary,
        files=spec.files,
        archive=spec.archive,
    )


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeServer]:
    """Local fake download server; the registry points at it (no internet)."""
    face_zip = make_zip({"buffalo_l/" + p: d for p, d in FACE_FILES.items()})
    files = {"/" + p: d for p, d in EMBED_FILES.items()} | {"/buffalo_l.zip": face_zip}
    s = FakeServer(files).start()
    embed = _as(make_spec(s.base_url, EMBED_FILES), EMBED_MODEL_ID, True)
    face = _as(make_spec(s.base_url, FACE_FILES, ("buffalo_l.zip", face_zip)), FACE_MODEL_ID, False)
    monkeypatch.setitem(REGISTRY, EMBED_MODEL_ID, embed)
    monkeypatch.setitem(REGISTRY, FACE_MODEL_ID, face)
    yield s
    s.stop()


def _models(client: TestClient) -> dict[str, dict[str, Any]]:
    return {m["id"]: m for m in client.get("/api/setup/models").json()}


def _wait_status(client: TestClient, model_id: str, want: str) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        m = _models(client)[model_id]
        if m["status"] == want:
            return m
        time.sleep(0.05)
    raise AssertionError(f"{model_id} never reached {want}: {m}")


def test_models_api_lists_registry_with_status(client: TestClient) -> None:
    models = _models(client)
    assert set(models) == {EMBED_MODEL_ID, FACE_MODEL_ID}
    embed = models[EMBED_MODEL_ID]
    assert embed["status"] == "missing" and embed["required"] is True
    assert models[FACE_MODEL_ID]["required"] is False
    for m in models.values():
        for key in ("name", "purpose", "size", "license_name", "license_url", "license_summary"):
            assert m[key], key
        assert {"done", "total", "bytes_per_s", "error"} <= set(m)
    assert embed["license_name"] == "Apache-2.0"
    assert embed["size"] == REGISTRY[EMBED_MODEL_ID].total_size


def test_install_refused_without_license(client: TestClient) -> None:
    r = client.post("/api/setup/models/embeddinggemma-2/install", json={"accept_license": False})
    assert r.status_code == 400
    assert client.post("/api/setup/models/embeddinggemma-2/install", json={}).status_code == 400
    assert (
        client.post("/api/setup/models/nope/install", json={"accept_license": True}).status_code
        == 404
    )


def test_install_runs_and_records_license(
    client: TestClient, server: FakeServer, settings: Settings
) -> None:
    r = client.post(f"/api/setup/models/{EMBED_MODEL_ID}/install", json={"accept_license": True})
    assert r.status_code == 202
    m = _wait_status(client, EMBED_MODEL_ID, "installed")
    assert m["done"] == m["total"] == REGISTRY[EMBED_MODEL_ID].total_size
    entry = read_installed(settings)[EMBED_MODEL_ID]
    assert entry["license_accepted_at"]
    assert (settings.models_dir / EMBED_MODEL_ID / "config.json").read_bytes() == EMBED_FILES[
        "config.json"
    ]
    # the optional face model installs from its archive the same way
    r = client.post(f"/api/setup/models/{FACE_MODEL_ID}/install", json={"accept_license": True})
    assert r.status_code == 202
    _wait_status(client, FACE_MODEL_ID, "installed")
    assert (settings.face_model_path / "det_10g.onnx").is_file()


def test_install_error_and_cancel_are_reported(
    client: TestClient, server: FakeServer, settings: Settings
) -> None:
    real = EMBED_FILES["model.safetensors"]
    server.files["/model.safetensors"] = bytes(len(real))  # right size, wrong hash
    client.post(f"/api/setup/models/{EMBED_MODEL_ID}/install", json={"accept_license": True})
    m = _wait_status(client, EMBED_MODEL_ID, "error")
    assert "corrupted" in m["error"]
    installer = client.app.state.model_installer
    installer.cancel(EMBED_MODEL_ID)  # nothing running: no-op
    assert client.post(f"/api/setup/models/{EMBED_MODEL_ID}/cancel").status_code == 200
    assert _models(client)[EMBED_MODEL_ID]["status"] == "error"
    assert EMBED_MODEL_ID not in read_installed(settings)


def test_cancelled_install_reports_missing_not_error(
    client: TestClient, server: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import model_install

    def slow_install(settings, spec, progress, cancel, **kw):  # noqa: ANN001, ANN003, ANN202
        progress.total, progress.done = spec.total_size, 7
        cancel.wait(10)
        raise model_install.InstallCancelled("Download cancelled")

    monkeypatch.setattr(model_install, "install_model", slow_install)
    client.post(f"/api/setup/models/{EMBED_MODEL_ID}/install", json={"accept_license": True})
    _wait_status(client, EMBED_MODEL_ID, "downloading")
    with pytest.raises(model_install.InstallError):
        client.app.state.model_installer.start(EMBED_MODEL_ID)  # already running
    again = client.post(
        f"/api/setup/models/{EMBED_MODEL_ID}/install", json={"accept_license": True}
    )
    assert again.status_code == 409
    assert client.post(f"/api/setup/models/{EMBED_MODEL_ID}/cancel").status_code == 200
    m = _wait_status(client, EMBED_MODEL_ID, "missing")
    assert m["error"] is None and m["done"] == 7 and m["total"] > 7


def test_embed_install_resumes_pending_analysis(client: TestClient, server: FakeServer) -> None:
    embedder: FakeEmbedder = client.app.state.embedder
    embedder.unavailable = True
    files = [("files", ("a.jpg", photo_bytes(1), "application/octet-stream"))]
    assert client.post("/api/upload", files=files).status_code == 200
    assert client.app.state.worker.wait_idle(timeout=30)
    assert client.get("/api/status").json()["pending"] == 1
    embedder.unavailable = False
    client.post(f"/api/setup/models/{EMBED_MODEL_ID}/install", json={"accept_license": True})
    deadline = time.monotonic() + 20
    while client.get("/api/status").json()["pending"] != 0:
        assert time.monotonic() < deadline, "pending files were not resumed"
        time.sleep(0.05)
    assert client.get("/api/status").json()["done"] == 1


def test_page_reachable_in_setup_mode_and_after(tmp_path: Path, client: TestClient) -> None:
    with TestClient(
        create_app(Settings(data_dir=tmp_path / "x"), FakeEmbedder(), True), base_url=BASE_URL
    ) as fresh:
        r = fresh.get("/setup/models", follow_redirects=False)
        assert r.status_code == 200 and "/static/setup_models.js" in r.text
        assert fresh.get("/api/setup/models").status_code == 200
        r = fresh.post(f"/api/setup/models/{EMBED_MODEL_ID}/install", json={"accept_license": True})
        assert r.status_code == 409
        r = fresh.post(f"/api/setup/models/{EMBED_MODEL_ID}/import", json={"zip_path": "x.zip"})
        assert r.status_code == 409
    r = client.get("/setup/models", follow_redirects=False)
    assert r.status_code == 200 and "/static/setup_models.js" in r.text
    assert 'href="/groups"' in r.text  # normal nav once set up


@pytest.fixture
def small_embed(monkeypatch: pytest.MonkeyPatch) -> ModelSpec:
    """A tiny offline embed spec, so tests don't allocate the real 1.5 GB of files."""
    spec = _as(make_spec("http://unused.invalid", EMBED_FILES), EMBED_MODEL_ID, True)
    monkeypatch.setitem(REGISTRY, EMBED_MODEL_ID, spec)
    return spec


def _place(settings: Settings, spec: ModelSpec, skip: str | None = None) -> Path:
    d = settings.models_dir / spec.id
    for f in spec.files:
        if f.path != skip:
            (d / f.path).parent.mkdir(parents=True, exist_ok=True)
            (d / f.path).write_bytes(EMBED_FILES[f.path])
    return d


def test_banner_when_required_model_missing(
    client: TestClient, settings: Settings, small_embed: ModelSpec
) -> None:
    assert 'href="/setup/models"' in client.get("/").text
    assert "Similar-photo detection needs the EmbeddingGemma model." in client.get("/").text
    assert "Similar-photo detection needs" not in client.get("/setup/models").text
    _place(settings, small_embed)
    assert "Similar-photo detection needs" not in client.get("/").text


def test_embed_model_source_prefers_local_dir(settings: Settings, small_embed: ModelSpec) -> None:
    assert settings.embed_model_source == settings.embed_model
    d = _place(settings, small_embed)
    assert settings.embed_model_source == str(d)


def test_embed_model_source_ignores_half_downloaded_dir(
    settings: Settings, small_embed: ModelSpec
) -> None:
    d = _place(settings, small_embed, skip="model.safetensors")
    assert (d / "config.json").is_file()
    assert settings.embed_model_source == settings.embed_model
    (d / "model.safetensors.partial").write_bytes(EMBED_FILES["model.safetensors"][:10])
    assert settings.embed_model_source == settings.embed_model


def test_face_model_installed_during_first_run_is_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server: FakeServer
) -> None:
    """The engine follows settings.data_dir chosen at /setup, without a restart."""
    monkeypatch.setenv("OGPHOTOS_CONFIG_FILE", str(tmp_path / "cfg" / "config.json"))
    monkeypatch.setattr(locations, "legacy_config_path", lambda: tmp_path / "no-legacy.json")
    monkeypatch.setattr(locations, "LEGACY_DATA_DIR", tmp_path / "no-data")
    app = create_app(Settings(data_dir=tmp_path / "default"), FakeEmbedder(), needs_setup=True)
    assert isinstance(app.state.face_engine, InsightFaceEngine)
    with TestClient(app, base_url=BASE_URL) as c:
        lib = tmp_path / "chosen"
        assert c.post("/api/setup", json={"path": str(lib)}).status_code == 200
        z = tmp_path / "face.zip"
        z.write_bytes(make_zip(FACE_FILES))
        r = c.post(f"/api/setup/models/{FACE_MODEL_ID}/import", json={"zip_path": str(z)})
        assert r.status_code == 200
        assert app.state.face_engine.available() is True
        assert app.state.face_engine.info()["dir"] == str(lib / "models" / FACE_MODEL_ID)


def test_import_zip_endpoint_error_message(
    client: TestClient, server: FakeServer, tmp_path: Path, settings: Settings
) -> None:
    tampered = dict(FACE_FILES, **{"w600k_r50.onnx": b"evil" * 75})
    bad = tmp_path / "bad.zip"
    bad.write_bytes(make_zip(tampered))
    r = client.post(f"/api/setup/models/{FACE_MODEL_ID}/import", json={"zip_path": str(bad)})
    assert r.status_code == 400 and "corrupted" in r.json()["detail"]
    assert bad.exists()  # the user's zip is never touched
    good = tmp_path / "good.zip"
    good.write_bytes(make_zip(FACE_FILES))
    r = client.post(f"/api/setup/models/{FACE_MODEL_ID}/import", json={"zip_path": f'"{good}"'})
    assert r.status_code == 200 and r.json()["status"] == "installed"
    assert read_installed(settings)[FACE_MODEL_ID]["license_accepted_at"]
    assert good.exists()
