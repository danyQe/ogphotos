import json
import sqlite3
from pathlib import Path

import pytest

from app.config import Settings
from app.db import init_db
from app.services import locations


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # The per-user default location, redirected (an OGPHOTOS_CONFIG_FILE override would
    # disable the migration under test).
    monkeypatch.delenv("OGPHOTOS_CONFIG_FILE", raising=False)
    monkeypatch.setattr(locations, "config_path", lambda: tmp_path / "config" / "config.json")
    monkeypatch.delenv("OGPHOTOS_DATA_DIR", raising=False)
    return tmp_path


def _legacy_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data_dir: str) -> Path:
    old = tmp_path / "old" / "config.json"
    old.parent.mkdir()
    old.write_text(json.dumps({"data_dir": data_dir}))
    monkeypatch.setattr(locations, "legacy_config_path", lambda: old)
    return old


def test_legacy_config_copied_once(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = _legacy_config(home, monkeypatch, "X:/lib")
    assert locations.migrate_legacy_config() is True
    assert json.loads(locations.config_path().read_text())["data_dir"] == "X:/lib"
    assert old.exists()  # copied, never moved or deleted
    assert locations.migrate_legacy_config() is False


def test_legacy_config_ignored_when_new_exists(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _legacy_config(home, monkeypatch, "X:/old")
    new = locations.config_path()
    new.parent.mkdir(parents=True)
    new.write_text('{"data_dir": "Y:/new"}')
    assert locations.migrate_legacy_config() is False
    assert json.loads(new.read_text())["data_dir"] == "Y:/new"


def test_legacy_config_missing_is_noop(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(locations, "legacy_config_path", lambda: home / "nope.json")
    assert locations.migrate_legacy_config() is False
    assert not locations.config_path().exists()


def test_legacy_config_not_copied_into_explicit_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OGPHOTOS_CONFIG_FILE means "use exactly this file": the legacy config stays out."""
    override = tmp_path / "override" / "config.json"
    monkeypatch.setenv("OGPHOTOS_CONFIG_FILE", str(override))
    _legacy_config(tmp_path, monkeypatch, "X:/real-library")
    assert locations.migrate_legacy_config() is False
    assert not override.exists()


def test_legacy_db_renamed_with_wal_files(tmp_path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        (tmp_path / f"locallens.db{suffix}").write_bytes(b"x")
    assert locations.migrate_legacy_db(tmp_path) is True
    assert (tmp_path / "ogphotos.db").exists() and not (tmp_path / "locallens.db").exists()
    assert (tmp_path / "ogphotos.db-wal").exists()
    assert (tmp_path / "ogphotos.db-shm").exists()


def test_legacy_db_left_alone_when_new_exists(tmp_path: Path) -> None:
    (tmp_path / "locallens.db").write_bytes(b"old")
    (tmp_path / "ogphotos.db").write_bytes(b"new")
    assert locations.migrate_legacy_db(tmp_path) is False
    assert (tmp_path / "locallens.db").read_bytes() == b"old"
    assert (tmp_path / "ogphotos.db").read_bytes() == b"new"


def test_init_db_migrates_legacy_db(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    engine, _ = init_db(settings)
    engine.dispose()
    settings.db_path.rename(tmp_path / "locallens.db")
    engine, _ = init_db(settings)
    engine.dispose()
    assert settings.db_path.exists() and not (tmp_path / "locallens.db").exists()


def test_photo_count_recognizes_legacy_library(tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / "locallens.db") as conn:
        conn.execute("CREATE TABLE files (id INTEGER, is_trashed BOOLEAN)")
        conn.execute("INSERT INTO files VALUES (1, 0)")
    conn.close()
    assert locations.photo_count(tmp_path) == 1


def test_resolve_settings_reads_migrated_config(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _legacy_config(home, monkeypatch, str(home / "lib"))
    settings, needs = locations.resolve_settings()
    assert not needs
    assert settings.data_dir == home / "lib"


def test_legacy_db_rename_failure_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for suffix in ("", "-wal", "-shm"):
        (tmp_path / f"locallens.db{suffix}").write_bytes(suffix.encode() or b"main")
    real_rename = Path.rename
    calls = 0

    def flaky(self: Path, target: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError("locked")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    with pytest.raises(locations.LocationError, match="close any running LocalLens"):
        locations.migrate_legacy_db(tmp_path)
    monkeypatch.undo()
    for suffix in ("", "-wal", "-shm"):
        assert (tmp_path / f"locallens.db{suffix}").exists()
        assert not (tmp_path / f"ogphotos.db{suffix}").exists()
