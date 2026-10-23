"""Jupyter-style launcher: `python -m app.launcher` starts the server, opens the browser,
prints a banner, and stops on Ctrl+C or when the window closes. A second launch finds the
running instance (via run/instance.json + /healthz) and just opens the browser."""

import json
import logging
import os
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, Any

from app.services.locations import app_home

log = logging.getLogger("ogphotos.launcher")

LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3
READY_TIMEOUT_S = 60.0
READY_POLL_S = 0.2
WAIT_OTHER_S = 60.0
WAIT_OTHER_POLL_S = 0.5

# Our own server is always local: never route the probe through a system/env proxy.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class NoFreePort(RuntimeError):
    pass


@dataclass
class Instance:
    pid: int
    port: int


def instance_file(home: Path) -> Path:
    return home / "run" / "instance.json"


def read_instance(home: Path) -> Instance | None:
    try:
        data = json.loads(instance_file(home).read_text(encoding="utf-8"))
        return Instance(pid=int(data["pid"]), port=int(data["port"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def probe(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> dict | None:
    """GET /healthz; the JSON only if an OGphotos server answered."""
    try:
        with _DIRECT.open(f"http://{host}:{port}/healthz", timeout=timeout) as resp:
            data = json.loads(resp.read())
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return data if isinstance(data, dict) and data.get("app") == "ogphotos" else None


def find_running(home: Path) -> Instance | None:
    """The live instance recorded in run/instance.json; a stale file is deleted."""
    inst = read_instance(home)
    if inst is not None and probe(inst.port) is not None:
        return inst
    instance_file(home).unlink(missing_ok=True)
    return None


def pick_port(host: str, start: int, end: int) -> int:
    """First port in start..end that binds (just `start` if the range is inverted)."""
    for port in range(start, max(start, end) + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
            except OSError:
                continue
            return port
    raise NoFreePort(f"Ports {start}–{end} are all in use.")


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return True
    return False


def _startup_failure(web: Any, host: str, port: int, log_path: Path) -> str:
    """uvicorn exits (SystemExit) both when the port won't bind and when the app's startup
    fails; tell the two apart so the user isn't sent hunting for a port conflict."""
    error = getattr(getattr(web, "state", None), "startup_error", None)
    if error is None and port_in_use(host, port):
        return f"Could not bind port {port}."
    if error is None:
        reason = "see the log for details"
    else:
        reason = str(error) or type(error).__name__
    return f"OGphotos could not start: {reason}. Log: {log_path}"


def configure_logging(home: Path) -> Path:
    """Console + rotating file at <home>/logs/ogphotos.log."""
    path = home / "logs" / "ogphotos.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        path, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)
    return path


def acquire_lock(home: Path) -> IO[bytes] | None:
    """Exclusive OS lock on run/launcher.lock, held until the handle is closed (the OS drops
    it if we crash). None means another launcher already holds it."""
    path = home / "run" / "launcher.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")  # noqa: SIM115 - kept open for the lock's lifetime
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _wait_for_other_launcher(home: Path) -> Instance | None:
    """Another launcher is starting up: wait for its server to answer."""
    deadline = time.monotonic() + WAIT_OTHER_S
    while True:
        if (running := find_running(home)) is not None:
            return running
        if time.monotonic() >= deadline:
            return None
        time.sleep(WAIT_OTHER_POLL_S)


def _open_browser(url: str) -> None:
    if os.environ.get("OGPHOTOS_NO_BROWSER") != "1":
        webbrowser.open(url)


def _write_instance(home: Path, port: int) -> None:
    path = instance_file(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": os.getpid(), "port": port}), encoding="utf-8")
    tmp.replace(path)


def _announce_when_ready(server: Any, home: Path, host: str, port: int, version: str) -> None:
    """Once *our* server has started (not just something answering on the port), record it,
    print the banner and open the browser."""
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline and not server.should_exit:
        if server.started and probe(port, host) is not None:
            url = f"http://{host}:{port}"
            _write_instance(home, port)
            print(f"OGphotos {version} running at {url}", flush=True)
            print("Close this window or press Ctrl+C to stop.", flush=True)
            _open_browser(url + "/")
            return
        time.sleep(READY_POLL_S)
    if not server.should_exit:
        log.error("server did not start within %.0f s", READY_TIMEOUT_S)


def _fail(message: str, log_path: Path) -> int:
    if message:
        print(message)
    print(f"OGphotos stopped with an error. Log: {log_path}")
    if os.environ.get("OGPHOTOS_NONINTERACTIVE") != "1":
        try:
            input("Press Enter to close…")
        except (EOFError, OSError):  # no usable stdin (closed or redirected)
            pass
    return 1


def _serve(home: Path, log_path: Path) -> int:
    import uvicorn

    import app.main as app_main
    from app import __version__
    from app.services.locations import resolve_settings

    settings, needs_setup = resolve_settings()
    port = pick_port(settings.host, settings.port, settings.port_range_end)
    web = app_main.create_app(settings, needs_setup=needs_setup)
    server = uvicorn.Server(uvicorn.Config(web, host=settings.host, port=port, log_level="info"))
    threading.Thread(
        target=_announce_when_ready,
        args=(server, home, settings.host, port, __version__),
        name="launcher-ready",
        daemon=True,
    ).start()
    try:
        server.run()
    except SystemExit:  # uvicorn exits instead of raising (bind or app startup failure)
        return _fail(_startup_failure(web, settings.host, port, log_path), log_path)
    except KeyboardInterrupt:  # uvicorn re-raises Ctrl+C after its graceful shutdown
        return 0
    finally:
        inst = read_instance(home)
        if inst is not None and inst.pid == os.getpid():
            instance_file(home).unlink(missing_ok=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    # Before anything imports the ML stack: models come from disk, never the network.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    home = app_home()
    log_path = configure_logging(home)
    lock = None
    try:
        lock = acquire_lock(home)
        if lock is None:  # another launcher is starting or running
            running = _wait_for_other_launcher(home)
            if running is None:
                return _fail("OGphotos is still starting in another window.", log_path)
        elif (running := find_running(home)) is None:
            return _serve(home, log_path)
        url = f"http://127.0.0.1:{running.port}/"
        _open_browser(url)
        print(f"OGphotos already running at {url}")
        time.sleep(3)
        return 0
    except KeyboardInterrupt:  # Ctrl+C is a normal stop, at any point
        return 0
    except NoFreePort as e:
        return _fail(str(e), log_path)
    except Exception:
        log.error("launcher crashed\n%s", traceback.format_exc())
        return _fail("", log_path)
    finally:
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    sys.exit(main())
