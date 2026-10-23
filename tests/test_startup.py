import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from tests.fakes import BASE_URL, FakeEmbedder


def test_data_dirs_created(client: TestClient, settings: Settings) -> None:
    for d in (settings.library_dir, settings.thumbs_dir, settings.trash_dir, settings.tmp_dir):
        assert d.is_dir()
    assert settings.db_path.is_file()


def test_wal_and_tables(client: TestClient, settings: Settings) -> None:
    con = sqlite3.connect(settings.db_path)
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"files", "groups", "group_exclusions", "settings"} <= tables
        seeded = dict(con.execute("SELECT key, value FROM settings"))
    finally:
        con.close()
    assert seeded["phash_max_distance"] == "8"
    assert seeded["embed_min_cosine"] == "0.92"
    assert seeded["embed_dim"] == "512"


def test_lifespan_failure_is_recorded_for_the_launcher(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.main as app_main

    def broken(app: object) -> None:
        raise RuntimeError("database disk image is malformed")

    monkeypatch.setattr(app_main, "_start_services", broken)
    web = app_main.create_app(settings, FakeEmbedder(settings.embed_dim), needs_setup=False)
    with pytest.raises(RuntimeError), TestClient(web, base_url=BASE_URL):
        pass
    assert str(web.state.startup_error) == "database disk image is malformed"


def test_importing_main_has_no_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """No module-level app: importing app.main must not read or migrate any config."""
    import importlib

    import app.main as app_main
    from app.services import locations

    def forbidden() -> None:
        raise AssertionError("resolve_settings called at import time")

    monkeypatch.setattr(locations, "resolve_settings", forbidden)
    try:
        importlib.reload(app_main)
    finally:
        monkeypatch.undo()
        importlib.reload(app_main)
    assert not hasattr(app_main, "app")
