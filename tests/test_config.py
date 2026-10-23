from pathlib import Path

import pytest

from app.config import Settings


def test_defaults_bind_localhost() -> None:
    s = Settings()
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.embed_dim == 512


def test_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OGPHOTOS_DATA_DIR", str(tmp_path / "x"))
    s = Settings()
    assert s.data_dir == tmp_path / "x"
    assert s.db_path == tmp_path / "x" / "ogphotos.db"


def test_env_prefix_is_ogphotos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OGPHOTOS_PORT", "9001")
    assert Settings().port == 9001


def test_version_matches_pyproject() -> None:
    import tomllib

    import app

    root = Path(__file__).resolve().parent.parent
    meta = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert app.__version__ == meta["project"]["version"]


def test_models_dir_under_data_dir(tmp_path: Path) -> None:
    s = Settings(data_dir=tmp_path)
    assert s.models_dir == tmp_path / "models"
    assert s.face_model_path == tmp_path / "models" / "buffalo_l"


def test_port_range_end_default() -> None:
    assert Settings().port_range_end == 8010


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("win32", Path("LOCAL") / "OGphotos"),
        ("darwin", Path("HOME") / "Library" / "Application Support" / "OGphotos"),
        ("linux", Path("HOME") / ".local" / "share" / "ogphotos"),
    ],
)
def test_app_home_per_os(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, expected: Path
) -> None:
    from app.services import locations

    monkeypatch.delenv("OGPHOTOS_HOME", raising=False)
    monkeypatch.setattr(locations.sys, "platform", platform)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LOCAL"))
    monkeypatch.setattr(locations.Path, "home", lambda: tmp_path / "HOME")
    expected = tmp_path / expected
    assert locations.app_home() == expected
    assert not expected.exists()


def test_app_home_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.services import locations

    monkeypatch.setenv("OGPHOTOS_HOME", str(tmp_path / "custom"))
    assert locations.app_home() == tmp_path / "custom"
