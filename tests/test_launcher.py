import http.server
import json
import os
import socket
import threading
import webbrowser
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from app import launcher
from app.launcher import (
    Instance,
    NoFreePort,
    find_running,
    instance_file,
    main,
    pick_port,
    read_instance,
)


@contextmanager
def fake_server(payload: object) -> Iterator[int]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def write_instance(home: Path, pid: int, port: int) -> None:
    path = instance_file(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "port": port}), encoding="utf-8")


def test_pick_port_skips_busy() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        busy = s.getsockname()[1]
        assert pick_port("127.0.0.1", busy, busy + 5) != busy


def test_pick_port_all_busy_raises() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        busy = s.getsockname()[1]
        with pytest.raises(NoFreePort):
            pick_port("127.0.0.1", busy, busy)


def test_pick_port_inverted_range_tries_start_only() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        busy = s.getsockname()[1]
        with pytest.raises(NoFreePort):
            pick_port("127.0.0.1", busy, busy - 3)


def test_read_instance_missing_or_garbage(tmp_path: Path) -> None:
    assert read_instance(tmp_path) is None
    path = instance_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    assert read_instance(tmp_path) is None


def test_probe_returns_ogphotos_payload_only() -> None:
    with fake_server({"app": "ogphotos", "version": "x"}) as port:
        assert launcher.probe(port) == {"app": "ogphotos", "version": "x"}
    with fake_server({"app": "other"}) as port:
        assert launcher.probe(port) is None
    with fake_server(["ogphotos"]) as port:
        assert launcher.probe(port) is None


def test_find_running_deletes_stale_file_dead_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_instance(tmp_path, 1, 8999)
    monkeypatch.setattr(launcher, "probe", lambda *a, **k: None)
    assert find_running(tmp_path) is None
    assert not instance_file(tmp_path).exists()


def test_find_running_other_app_is_stale(tmp_path: Path) -> None:
    with fake_server({"app": "other"}) as port:
        write_instance(tmp_path, 1, port)
        assert find_running(tmp_path) is None
    assert not instance_file(tmp_path).exists()


def test_find_running_live_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_instance(tmp_path, 42, 8123)
    monkeypatch.setattr(launcher, "probe", lambda *a, **k: {"app": "ogphotos"})
    assert find_running(tmp_path) == Instance(42, 8123)
    assert instance_file(tmp_path).exists()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OGPHOTOS_HOME", str(tmp_path))
    monkeypatch.setenv("OGPHOTOS_NONINTERACTIVE", "1")
    monkeypatch.setenv("OGPHOTOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OGPHOTOS_CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(launcher, "configure_logging", lambda h: h / "logs" / "ogphotos.log")
    return tmp_path


def test_main_existing_instance_opens_browser_and_exits(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    opened: list[str] = []
    monkeypatch.delenv("OGPHOTOS_NO_BROWSER", raising=False)
    monkeypatch.setattr(webbrowser, "open", opened.append)
    monkeypatch.setattr(launcher, "find_running", lambda h: Instance(1, 8123))
    assert main([]) == 0
    assert opened == ["http://127.0.0.1:8123/"]
    assert "OGphotos already running at http://127.0.0.1:8123/" in capsys.readouterr().out


def test_main_no_browser_env(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setenv("OGPHOTOS_NO_BROWSER", "1")
    monkeypatch.setattr(webbrowser, "open", opened.append)
    monkeypatch.setattr(launcher, "find_running", lambda h: Instance(1, 8123))
    assert main([]) == 0
    assert opened == []


def test_main_sets_offline_env(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "0")  # a pre-set value wins
    monkeypatch.setenv("OGPHOTOS_NO_BROWSER", "1")
    monkeypatch.setattr(launcher, "find_running", lambda h: Instance(1, 8123))
    assert main([]) == 0
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "0"
    monkeypatch.delenv("HF_HUB_OFFLINE")


def test_crash_pauses_unless_noninteractive(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import app.main as app_main

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("boom")

    def no_input(*a: object) -> str:
        raise AssertionError("input() must not be called")

    monkeypatch.setattr(launcher, "find_running", lambda h: None)
    monkeypatch.setattr(app_main, "create_app", boom)
    monkeypatch.setattr("builtins.input", no_input)
    assert main([]) == 1
    assert "OGphotos stopped with an error. Log:" in capsys.readouterr().out


def test_no_free_port_message(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def full(*a: object) -> int:
        raise NoFreePort("Ports 1–2 are all in use.")

    monkeypatch.setattr(launcher, "find_running", lambda h: None)
    monkeypatch.setattr(launcher, "pick_port", full)
    assert main([]) == 1
    out = capsys.readouterr().out
    assert "are all in use." in out


def test_configure_logging_writes_file(tmp_path: Path) -> None:
    import logging

    root = logging.getLogger()
    before = list(root.handlers)
    try:
        path = launcher.configure_logging(tmp_path)
        logging.getLogger("x").warning("hello")
        for h in root.handlers:
            h.flush()
        assert path == tmp_path / "logs" / "ogphotos.log"
        assert "hello" in path.read_text(encoding="utf-8")
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_acquire_lock_is_exclusive(tmp_path: Path) -> None:
    first = launcher.acquire_lock(tmp_path)
    assert first is not None
    try:
        assert launcher.acquire_lock(tmp_path) is None
    finally:
        first.close()
    again = launcher.acquire_lock(tmp_path)
    assert again is not None
    again.close()


def test_main_lock_held_waits_for_other_and_skips_startup(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.main as app_main

    opened: list[str] = []
    calls: list[str] = []
    monkeypatch.delenv("OGPHOTOS_NO_BROWSER", raising=False)
    monkeypatch.setattr(webbrowser, "open", opened.append)
    monkeypatch.setattr(launcher, "pick_port", lambda *a: calls.append("pick_port") or 1)
    monkeypatch.setattr(app_main, "create_app", lambda *a, **k: calls.append("create_app"))
    answers = iter([None, None, Instance(7, 8124)])
    monkeypatch.setattr(launcher, "find_running", lambda h: next(answers))
    held = launcher.acquire_lock(home)
    assert held is not None
    try:
        assert main([]) == 0
    finally:
        held.close()
    assert calls == []
    assert opened == ["http://127.0.0.1:8124/"]


def test_main_lock_held_timeout_fails(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(launcher, "WAIT_OTHER_S", 0.0)
    monkeypatch.setattr(launcher, "find_running", lambda h: None)
    held = launcher.acquire_lock(home)
    assert held is not None
    try:
        assert main([]) == 1
    finally:
        held.close()
    assert "still starting in another window" in capsys.readouterr().out


class _ExitingServer:
    """uvicorn.Server stand-in that exits the way uvicorn does on any startup failure."""

    started = False
    should_exit = False

    def __init__(self, config: object) -> None:
        pass

    def run(self) -> None:
        raise SystemExit(3)


def _fake_web(error: BaseException | None) -> object:
    class State:
        startup_error = error

    class Web:
        state = State()

    return Web()


def _run_exiting_server(
    monkeypatch: pytest.MonkeyPatch, port: int, error: BaseException | None
) -> int:
    import uvicorn

    import app.main as app_main

    monkeypatch.setattr(launcher, "find_running", lambda h: None)
    monkeypatch.setattr(launcher, "pick_port", lambda *a: port)
    monkeypatch.setattr(app_main, "create_app", lambda *a, **k: _fake_web(error))
    monkeypatch.setattr(uvicorn, "Config", lambda *a, **k: object())
    monkeypatch.setattr(uvicorn, "Server", _ExitingServer)
    monkeypatch.setattr(launcher, "_announce_when_ready", lambda *a: None)
    return main([])


def test_bind_failure_reports_error(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        assert _run_exiting_server(monkeypatch, port, None) == 1
    out = capsys.readouterr().out
    assert f"Could not bind port {port}." in out
    assert "could not start" not in out


def test_startup_failure_with_free_port_reports_reason(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    error = RuntimeError("database disk image is malformed")
    assert _run_exiting_server(monkeypatch, port, error) == 1
    out = capsys.readouterr().out
    log_path = home / "logs" / "ogphotos.log"
    assert f"OGphotos could not start: database disk image is malformed. Log: {log_path}" in out
    assert "Could not bind" not in out


def test_startup_failure_without_captured_error_still_generic(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert _run_exiting_server(monkeypatch, port, None) == 1
    out = capsys.readouterr().out
    assert "OGphotos could not start: " in out
    assert "Could not bind" not in out


def test_announcer_waits_for_server_started(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Srv:
        started = False
        should_exit = False

    monkeypatch.setattr(launcher, "probe", lambda *a, **k: {"app": "ogphotos"})
    monkeypatch.setattr(launcher, "READY_TIMEOUT_S", 0.05)
    monkeypatch.setenv("OGPHOTOS_NO_BROWSER", "1")
    srv = Srv()
    launcher._announce_when_ready(srv, home, "127.0.0.1", 8000, "9.9")
    assert not instance_file(home).exists()
    assert "running at" not in capsys.readouterr().out

    srv.started = True
    launcher._announce_when_ready(srv, home, "127.0.0.1", 8000, "9.9")
    assert read_instance(home) == Instance(os.getpid(), 8000)
    assert "OGphotos 9.9 running at http://127.0.0.1:8000" in capsys.readouterr().out


def test_crash_pauses_when_interactive(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.main as app_main

    prompts: list[str] = []

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.delenv("OGPHOTOS_NONINTERACTIVE")
    monkeypatch.setattr(launcher, "find_running", lambda h: None)
    monkeypatch.setattr(app_main, "create_app", boom)
    monkeypatch.setattr("builtins.input", lambda p="": prompts.append(p) or "")
    assert main([]) == 1
    assert len(prompts) == 1


def test_ctrl_c_stop_is_a_normal_exit(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """uvicorn re-raises the captured SIGINT after its graceful shutdown; that's a normal
    stop: exit 0, no pause, instance.json cleaned up."""
    import uvicorn

    import app.main as app_main

    class FakeServer:
        started = True
        should_exit = False

        def __init__(self, config: object) -> None:
            pass

        def run(self) -> None:
            write_instance(home, os.getpid(), 8000)
            raise KeyboardInterrupt

    def no_input(*a: object) -> str:
        raise AssertionError("input() must not be called")

    monkeypatch.delenv("OGPHOTOS_NONINTERACTIVE")
    monkeypatch.setattr("builtins.input", no_input)
    monkeypatch.setattr(launcher, "find_running", lambda h: None)
    monkeypatch.setattr(launcher, "pick_port", lambda *a: 8000)
    monkeypatch.setattr(app_main, "create_app", lambda *a, **k: object())
    monkeypatch.setattr(uvicorn, "Config", lambda *a, **k: object())
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(launcher, "_announce_when_ready", lambda *a: None)
    assert main([]) == 0
    assert not instance_file(home).exists()
    assert "error" not in capsys.readouterr().out


def test_ctrl_c_during_startup_is_a_normal_exit(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.main as app_main

    def interrupted(*a: object, **k: object) -> None:
        raise KeyboardInterrupt

    def no_input(*a: object) -> str:
        raise AssertionError("input() must not be called")

    monkeypatch.delenv("OGPHOTOS_NONINTERACTIVE")
    monkeypatch.setattr("builtins.input", no_input)
    monkeypatch.setattr(launcher, "find_running", lambda h: None)
    monkeypatch.setattr(app_main, "create_app", interrupted)
    assert main([]) == 0


def test_probe_ignores_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A system proxy must not make our own local server look dead."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    with fake_server({"app": "ogphotos", "version": "x"}) as port:
        assert launcher.probe(port) == {"app": "ogphotos", "version": "x"}


@pytest.mark.parametrize("error", [EOFError, OSError])
def test_fail_survives_closed_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    def closed(*a: object) -> str:
        raise error

    monkeypatch.delenv("OGPHOTOS_NONINTERACTIVE", raising=False)
    monkeypatch.setattr("builtins.input", closed)
    assert launcher._fail("boom", tmp_path / "x.log") == 1
