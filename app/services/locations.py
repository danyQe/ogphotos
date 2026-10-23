"""Where the library lives: the per-user config file, OS picture folders, first-run choice.

The chosen data folder (library, thumbnails, trash, database) is remembered in a small JSON
file in the OS's per-user config location, outside the project:
  Windows  %APPDATA%\\OGphotos\\config.json
  macOS    ~/Library/Application Support/OGphotos/config.json
  Linux    $XDG_CONFIG_HOME/ogphotos/config.json (default ~/.config)
`OGPHOTOS_CONFIG_FILE` overrides the location; `OGPHOTOS_DATA_DIR` skips it entirely.
Installs from before the rename (LocalLens) are migrated on first start (never into an
`OGPHOTOS_CONFIG_FILE` override)."""

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings

log = logging.getLogger(__name__)

APP_FOLDER = "OGphotos"
LEGACY_APP_FOLDER = "LocalLens"
DB_NAME = "ogphotos.db"
LEGACY_DB_NAME = "locallens.db"
# The pre-setup default (`./data` next to the code); offered as "keep using it" on first run
LEGACY_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class LocationError(ValueError):
    """A folder that can't be used (message is shown to the user)."""


def _config_path(app_folder: str, linux_folder: str) -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / app_folder / "config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_folder / "config.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / linux_folder / "config.json"


def config_path() -> Path:
    if override := os.environ.get("OGPHOTOS_CONFIG_FILE"):
        return Path(override)
    return _config_path(APP_FOLDER, APP_FOLDER.lower())


def app_home() -> Path:
    """Per-user folder for the runtime (python/, env/, cache/, logs/, run/). Not created here.
    `OGPHOTOS_HOME` overrides it."""
    if override := os.environ.get("OGPHOTOS_HOME"):
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / APP_FOLDER
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_FOLDER
    return Path.home() / ".local" / "share" / APP_FOLDER.lower()


def legacy_config_path() -> Path:
    """Where the pre-rename (LocalLens) app kept its config file."""
    return _config_path(LEGACY_APP_FOLDER, LEGACY_APP_FOLDER.lower())


def migrate_legacy_config() -> bool:
    """Copy the old LocalLens config to the new location, once. The old file is kept.
    Skipped when `OGPHOTOS_CONFIG_FILE` is set: an explicit override means that file only."""
    if os.environ.get("OGPHOTOS_CONFIG_FILE"):
        return False
    new, old = config_path(), legacy_config_path()
    if new.exists() or not old.is_file():
        return False
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old, new)
    except OSError as e:
        log.warning("could not migrate config %s: %s", old, e)
        return False
    return True


def migrate_legacy_db(data_dir: Path) -> bool:
    """Rename locallens.db (and its WAL/SHM files) to ogphotos.db when the latter is absent."""
    old, new = data_dir / LEGACY_DB_NAME, data_dir / DB_NAME
    if new.exists() or not old.is_file():
        return False
    done: list[tuple[Path, Path]] = []
    # sidecars first, main file last: a half-done rename never leaves WAL data orphaned
    for suffix in ("-wal", "-shm", ""):
        src = data_dir / f"{LEGACY_DB_NAME}{suffix}"
        dst = data_dir / f"{DB_NAME}{suffix}"
        if not src.exists():
            continue
        try:
            src.rename(dst)
        except OSError as e:
            log.warning("could not rename %s to %s: %s", src, dst, e)
            for undo_src, undo_dst in reversed(done):
                try:
                    undo_dst.rename(undo_src)
                except OSError:
                    log.exception("could not roll back rename of %s", undo_src)
            raise LocationError(
                f"couldn't rename {LEGACY_DB_NAME} to {DB_NAME} — "
                f"close any running {LEGACY_APP_FOLDER} and try again"
            ) from e
        done.append((src, dst))
    return True


def load_config() -> dict[str, str]:
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("ignoring unreadable config %s: %s", path, e)
        return {}


def save_config(data_dir: Path) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = load_config() | {"data_dir": str(data_dir)}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def _windows_pictures() -> Path | None:
    """FOLDERID_Pictures via the shell, so a OneDrive-redirected Pictures folder is found."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    u = uuid.UUID("{33E28130-4E1E-4676-835A-98395C3BC3BB}")
    guid = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    out = ctypes.c_wchar_p()
    windll = ctypes.windll  # type: ignore[attr-defined]
    if windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)):
        return None
    try:
        return Path(out.value) if out.value else None
    finally:
        windll.ole32.CoTaskMemFree(out)


def _xdg_pictures() -> Path | None:
    home = Path.home()
    try:
        out = subprocess.run(
            ["xdg-user-dir", "PICTURES"], capture_output=True, text=True, timeout=2, check=True
        ).stdout.strip()
        if out and Path(out) != home:  # it answers $HOME when the folder isn't set
            return Path(out)
    except (OSError, subprocess.SubprocessError):
        pass
    dirs = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "user-dirs.dirs"
    try:
        for line in dirs.read_text(encoding="utf-8").splitlines():
            if line.startswith("XDG_PICTURES_DIR="):
                value = line.split("=", 1)[1].strip().strip('"').replace("$HOME", str(home))
                if value and Path(value) != home:
                    return Path(value)
    except OSError:
        pass
    return None


def pictures_dir() -> Path:
    """The user's Pictures folder on this OS (falls back to ~/Pictures)."""
    found = None
    try:
        if sys.platform == "win32":
            found = _windows_pictures()
        elif sys.platform != "darwin":
            found = _xdg_pictures()
    except Exception:
        log.exception("could not ask the OS for the Pictures folder")
    return found or Path.home() / "Pictures"


def default_data_dir() -> Path:
    return pictures_dir() / APP_FOLDER


def resolve_settings() -> tuple[Settings, bool]:
    """Settings for a normal start, plus whether the first-run setup is still needed."""
    migrate_legacy_config()
    settings = Settings()
    if os.environ.get("OGPHOTOS_DATA_DIR"):
        return settings, False
    if chosen := load_config().get("data_dir"):
        settings.data_dir = Path(chosen)
        return settings, False
    settings.data_dir = default_data_dir()
    return settings, True


def photo_count(folder: Path) -> int | None:
    """Live files in an OGphotos folder, or None if it isn't one."""
    db = next((p for p in (folder / DB_NAME, folder / LEGACY_DB_NAME) if p.is_file()), None)
    if db is None:
        return None
    try:
        uri = f"{db.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:  # `with conn` alone never closes
            row = conn.execute("SELECT count(*) FROM files WHERE NOT is_trashed").fetchone()
            return int(row[0])
    except sqlite3.Error:
        return 0


@dataclass
class Choice:
    path: Path
    exists: bool
    photos: int | None  # not None → an existing OGphotos library


def nearest_existing(path: Path) -> Path:
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def _foreign(path: Path) -> bool:
    """A non-empty folder that isn't an OGphotos library."""
    return path.is_dir() and photo_count(path) is None and any(path.iterdir())


def check_choice(raw: str) -> Choice:
    """Normalize a typed or picked folder. A non-empty folder that isn't an OGphotos
    library (e.g. Pictures itself) gets an `OGphotos` subfolder instead."""
    text = raw.strip().strip('"')
    if not text:
        raise LocationError("choose a folder")
    path = Path(os.path.expandvars(text)).expanduser()
    if not path.is_absolute():
        raise LocationError("enter a full path, e.g. C:\\Users\\you\\Pictures\\OGphotos")
    path = Path(os.path.abspath(path))
    if path.exists() and not path.is_dir():
        raise LocationError(f"not a folder: {path}")
    if _foreign(path):
        path = path / APP_FOLDER
        if path.exists() and (not path.is_dir() or _foreign(path)):
            raise LocationError(f"{path} already exists and isn't an OGphotos library")
    if not os.access(nearest_existing(path), os.W_OK):
        raise LocationError(f"can't write to {nearest_existing(path)}")
    return Choice(path=path, exists=path.exists(), photos=photo_count(path))


def move_library(src: Path, dest: Path) -> None:
    """Move a whole data folder (rename only, so nothing is copied or deleted).

    `dest` must not exist yet, or be an empty folder, and be on the same drive."""
    if photo_count(src) is None:
        raise LocationError(f"no OGphotos library at {src}")
    if dest.resolve().is_relative_to(src.resolve()):
        raise LocationError("the new folder can't be inside the current library")
    if dest.exists():
        if any(dest.iterdir()):
            raise LocationError(f"{dest} is not empty")
    anchor = nearest_existing(dest.parent)
    if os.stat(src).st_dev != os.stat(anchor).st_dev:
        raise LocationError(
            "that folder is on another drive; move the library folder there yourself, "
            "then choose it here"
        )
    if dest.exists():
        dest.rmdir()  # empty folder only; rename needs the name free
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dest)
