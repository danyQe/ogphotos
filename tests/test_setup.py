import json
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import init_db
from app.main import create_app
from app.models import File
from app.routes import setup as setup_routes
from app.services import locations
from app.services.folder_picker import PickerUnavailable
from tests.fakes import BASE_URL, FakeEmbedder
from tests.imgutil import photo_bytes


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated config file, Pictures folder and legacy ./data location."""
    monkeypatch.setenv("OGPHOTOS_CONFIG_FILE", str(tmp_path / "config" / "config.json"))
    monkeypatch.delenv("OGPHOTOS_DATA_DIR", raising=False)
    monkeypatch.setattr(locations, "legacy_config_path", lambda: tmp_path / "no-legacy.json")
    monkeypatch.setattr(locations, "pictures_dir", lambda: tmp_path / "Pictures")
    monkeypatch.setattr(locations, "LEGACY_DATA_DIR", tmp_path / "project" / "data")
    (tmp_path / "Pictures").mkdir()
    return tmp_path


@pytest.fixture
def fresh(home: Path) -> Iterator[TestClient]:
    """An app that has never been set up."""
    settings, needs = locations.resolve_settings()
    assert needs
    with TestClient(
        create_app(settings, FakeEmbedder(), needs_setup=needs), base_url=BASE_URL
    ) as c:
        yield c


def _make_library(folder: Path, n: int = 2) -> None:
    engine, factory = init_db(Settings(data_dir=folder))
    with factory() as s:
        for i in range(n):
            rel = f"library/2021/p{i}.jpg"
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_bytes(photo_bytes(i))
            s.add(
                File(
                    sha256=f"{i:064d}",
                    original_name=f"p{i}.jpg",
                    stored_path=rel,
                    media_type="image",
                    mime="image/jpeg",
                    size_bytes=1,
                    uploaded_at=datetime(2021, 1, 1),
                    analysis_status="pending",
                )
            )
        s.commit()
    engine.dispose()


def test_setup_mode_sends_everything_to_setup(fresh: TestClient, home: Path) -> None:
    for path in ("/", "/groups", "/library", "/settings"):
        r = fresh.get(path, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/setup", path
    assert fresh.get("/api/groups").status_code == 503
    assert fresh.get("/static/style.css").status_code == 200
    page = fresh.get("/setup").text
    assert str(home / "Pictures" / "OGphotos") in page and "Browse" in page
    assert 'href="/groups"' not in page  # no nav before setup
    info = fresh.get("/api/setup").json()
    assert info["needs_setup"] is True and info["legacy"] is None
    assert info["default_path"] == str(home / "Pictures" / "OGphotos")


def test_check_choice(fresh: TestClient, home: Path) -> None:
    def check(path: str) -> dict:
        return fresh.post("/api/setup/check", json={"path": path}).json()

    target = home / "Pictures" / "OGphotos"
    assert check(str(target)) == {"path": str(target), "exists": False, "photos": None}
    # a non-empty ordinary folder gets an OGphotos subfolder
    (home / "Pictures" / "holiday.jpg").write_bytes(b"x")
    assert check(str(home / "Pictures"))["path"] == str(target)
    _make_library(home / "elsewhere", n=3)
    assert check(str(home / "elsewhere"))["photos"] == 3
    for bad in ("", "relative/dir", str(home / "Pictures" / "holiday.jpg")):
        assert fresh.post("/api/setup/check", json={"path": bad}).status_code == 400


def test_check_choice_foreign_subfolder_message(fresh: TestClient, home: Path) -> None:
    (home / "Pictures" / "OGphotos").mkdir(parents=True)
    (home / "Pictures" / "OGphotos" / "notes.txt").write_text("x")
    r = fresh.post("/api/setup/check", json={"path": str(home / "Pictures")})
    assert r.status_code == 400
    assert "isn't an OGphotos library" in r.json()["detail"]


def test_setup_new_library_starts_the_app(fresh: TestClient, home: Path) -> None:
    target = home / "Pictures" / "OGphotos"
    r = fresh.post("/api/setup", json={"path": str(target)})
    assert r.status_code == 200 and r.json() == {"data_dir": str(target), "photos": 0}
    assert (target / "library").is_dir() and (target / "ogphotos.db").is_file()
    config = json.loads((home / "config" / "config.json").read_text())
    assert config == {"data_dir": str(target)}

    assert fresh.get("/api/groups").status_code == 200
    assert fresh.get("/setup", follow_redirects=False).headers["location"] == "/"
    up = fresh.post("/api/upload", files=[("files", ("a.jpg", photo_bytes(1)))]).json()
    assert up["results"][0]["status"] == "new"
    assert list((target / "library").rglob("*.jpg"))
    assert fresh.post("/api/setup", json={"path": str(target)}).status_code == 409

    # next start reads the saved folder: no setup
    settings, needs = locations.resolve_settings()
    assert not needs and settings.data_dir == target


def test_keep_existing_library(fresh: TestClient, home: Path) -> None:
    _make_library(locations.LEGACY_DATA_DIR)
    page = fresh.get("/setup").text
    assert "2 photos" in page and "Keep using it" in page
    r = fresh.post("/api/setup", json={"path": str(locations.LEGACY_DATA_DIR)})
    assert r.json()["photos"] == 2
    assert fresh.get("/api/files").json()["total"] == 2


def test_move_existing_library(fresh: TestClient, home: Path) -> None:
    legacy = locations.LEGACY_DATA_DIR
    _make_library(legacy)
    target = home / "Pictures" / "OGphotos"
    r = fresh.post("/api/setup", json={"path": str(target), "move_existing": True})
    assert r.status_code == 200 and r.json()["photos"] == 2
    assert not legacy.exists() and (target / "library" / "2021" / "p0.jpg").is_file()
    [f] = [f for f in fresh.get("/api/files").json()["files"] if f["original_name"] == "p0.jpg"]
    assert fresh.get(f"/api/files/{f['file_id']}/original").status_code == 200


def test_move_refuses_a_non_empty_target(fresh: TestClient, home: Path) -> None:
    _make_library(locations.LEGACY_DATA_DIR)
    _make_library(home / "other")  # an existing library is not an empty target
    r = fresh.post("/api/setup", json={"path": str(home / "other"), "move_existing": True})
    assert r.status_code == 400 and "not empty" in r.json()["detail"]
    assert locations.LEGACY_DATA_DIR.exists()
    assert fresh.app.state.needs_setup is True


def test_browse_folder(fresh: TestClient, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    picked = home / "Pictures"
    monkeypatch.setattr(setup_routes, "pick_folder", lambda initial, title: picked)
    assert fresh.post("/api/browse-folder", json={}).json() == {"path": str(picked)}
    monkeypatch.setattr(setup_routes, "pick_folder", lambda initial, title: None)
    assert fresh.post("/api/browse-folder", json={}).json() == {"path": None}

    def unavailable(initial: Path, title: str) -> Path:
        raise PickerUnavailable("no display")

    monkeypatch.setattr(setup_routes, "pick_folder", unavailable)
    assert fresh.post("/api/browse-folder", json={}).status_code == 501


def test_resolve_settings_prefers_env(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    locations.save_config(home / "from-config")
    settings, needs = locations.resolve_settings()
    assert not needs and settings.data_dir == home / "from-config"
    monkeypatch.setenv("OGPHOTOS_DATA_DIR", str(home / "from-env"))
    settings, needs = locations.resolve_settings()
    assert not needs and settings.data_dir == home / "from-env"


def test_xdg_pictures_from_user_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_tool(*args: object, **kwargs: object) -> None:
        raise OSError("xdg-user-dir not installed")

    monkeypatch.setattr(subprocess, "run", no_tool)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: Path("/home/me"))
    (tmp_path / "user-dirs.dirs").write_text('XDG_PICTURES_DIR="$HOME/Bilder"\n')
    assert locations._xdg_pictures() == Path("/home/me/Bilder")
    (tmp_path / "user-dirs.dirs").write_text('XDG_PICTURES_DIR="$HOME"\n')
    assert locations._xdg_pictures() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows known folders")
def test_windows_pictures_folder_exists() -> None:
    found = locations._windows_pictures()
    assert found is not None and found.is_dir()


def test_cross_site_writes_are_refused(client: TestClient) -> None:
    evil = {"origin": "https://evil.example"}
    assert client.post("/api/trash/empty?confirm=true", headers=evil).status_code == 403
    assert (
        client.post("/api/trash/empty", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    )
    same = {"origin": "http://127.0.0.1"}
    assert client.post("/api/trash/empty?confirm=true", headers=same).status_code == 200
    assert client.get("/api/groups", headers=evil).status_code == 200  # reads are fine
