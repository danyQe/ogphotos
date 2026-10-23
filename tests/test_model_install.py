import hashlib
import io
import json
import threading
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.models_registry import ModelFile, ModelSpec
from app.services.model_install import (
    InstallCancelled,
    InstallError,
    Progress,
    import_zip,
    install_model,
    is_installed,
    model_dir,
    read_installed,
    record_install,
)
from tests.httpfake import FakeServer


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_spec(
    base: str, files: dict[str, bytes], archive: tuple[str, bytes] | None = None
) -> ModelSpec:
    mfiles = tuple(ModelFile(p, f"{base}/{p}", sha(d), len(d)) for p, d in files.items())
    arch = None
    if archive:
        name, data = archive
        arch = ModelFile(name, f"{base}/{name}", sha(data), len(data))
    return ModelSpec(
        id="m1",
        name="M",
        purpose="p",
        required=True,
        license_name="l",
        license_url="u",
        license_summary="s",
        files=mfiles,
        archive=arch,
    )


def make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


FILES = {"a.bin": bytes(range(256)) * 20, "sub/b.json": b'{"x": 1}'}


@pytest.fixture
def server() -> Iterator[FakeServer]:
    s = FakeServer({"/" + p: d for p, d in FILES.items()}).start()
    yield s
    s.stop()


def spec_for(server: FakeServer) -> ModelSpec:
    return make_spec(server.base_url, FILES)


def offline_spec() -> ModelSpec:
    return make_spec("http://unused.invalid", FILES)


def test_installs_and_verifies(settings: Settings, server: FakeServer) -> None:
    spec = spec_for(server)
    install_model(settings, spec, Progress(), threading.Event())
    d = model_dir(settings, spec)
    assert (d / "a.bin").read_bytes() == FILES["a.bin"]
    assert (d / "sub" / "b.json").read_bytes() == FILES["sub/b.json"]
    assert not list(d.rglob("*.partial"))
    assert is_installed(settings, spec)


def test_progress_reports_totals(settings: Settings, server: FakeServer) -> None:
    spec = spec_for(server)
    p = Progress()
    install_model(settings, spec, p, threading.Event())
    assert p.total == spec.total_size
    assert p.done == spec.total_size


def test_resume_after_interrupt_uses_range(settings: Settings, server: FakeServer) -> None:
    spec = spec_for(server)
    server.fail_after = 1000
    with pytest.raises(InstallError, match="Download failed"):
        install_model(settings, spec, Progress(), threading.Event())
    partial = model_dir(settings, spec) / "a.bin.partial"
    assert partial.stat().st_size == 1000
    server.requests.clear()
    p = Progress()
    install_model(settings, spec, p, threading.Event())
    assert p.done == spec.total_size
    assert any(r["range"] == "bytes=1000-" for r in server.requests)
    assert (model_dir(settings, spec) / "a.bin").read_bytes() == FILES["a.bin"]
    assert not partial.exists()


def test_server_ignoring_range_restarts_from_zero(settings: Settings, server: FakeServer) -> None:
    server.honor_range = False
    spec = spec_for(server)
    d = model_dir(settings, spec)
    d.mkdir(parents=True)
    (d / "a.bin.partial").write_bytes(b"junk" * 100)
    p = Progress()
    install_model(settings, spec, p, threading.Event())
    assert any(r["range"] == "bytes=400-" for r in server.requests)
    assert (d / "a.bin").read_bytes() == FILES["a.bin"]
    assert p.done == spec.total_size


def test_checksum_mismatch_deletes_partial(settings: Settings, server: FakeServer) -> None:
    spec = spec_for(server)
    server.files["/a.bin"] = b"x" * len(FILES["a.bin"])  # same size, wrong content
    with pytest.raises(InstallError, match="Download corrupted"):
        install_model(settings, spec, Progress(), threading.Event())
    d = model_dir(settings, spec)
    assert not (d / "a.bin").exists()
    assert not (d / "a.bin.partial").exists()
    assert not is_installed(settings, spec)


def test_insufficient_disk_space(
    settings: Settings, server: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = spec_for(server)
    free = int(spec.total_size * 1.1)
    monkeypatch.setattr(
        "shutil.disk_usage", lambda p: SimpleNamespace(total=free * 10, used=0, free=free)
    )
    with pytest.raises(InstallError, match="free space"):
        install_model(settings, spec, Progress(), threading.Event())
    assert server.requests == []


def test_insufficient_disk_space_counts_archive_and_extracted(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    members = {"det.onnx": b"d" * 4000, "rec.onnx": b"r" * 6000}
    archive = make_zip(members)
    s = FakeServer({"/arch.zip": archive}).start()
    try:
        spec = make_spec(s.base_url, members, archive=("arch.zip", archive))
        free = int(1.2 * (spec.total_size + 10000)) - 1  # enough for archive alone only
        assert free > 1.2 * spec.total_size
        monkeypatch.setattr(
            "shutil.disk_usage", lambda p: SimpleNamespace(total=free * 10, used=0, free=free)
        )
        with pytest.raises(InstallError, match="free space"):
            install_model(settings, spec, Progress(), threading.Event())
        assert s.requests == []
    finally:
        s.stop()


def corrupt_crc(data: bytes, member: str) -> bytes:
    """Flip a byte of a stored member's payload so its CRC-32 no longer matches."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        info = z.getinfo(member)
    buf = bytearray(data)
    buf[info.header_offset + 30 + len(info.filename.encode()) + 1] ^= 0xFF
    return bytes(buf)


def test_import_zip_crc_corrupted_member(settings: Settings, tmp_path: Path) -> None:
    spec = offline_spec()
    z = tmp_path / "crc.zip"
    z.write_bytes(corrupt_crc(make_zip(FILES), "sub/b.json"))
    with pytest.raises(InstallError):
        import_zip(settings, spec, z)
    d = model_dir(settings, spec)
    assert not [p for p in d.rglob("*") if p.is_file()]
    assert not is_installed(settings, spec)


def test_archive_install_crc_corrupted_member(settings: Settings) -> None:
    members = {"det.onnx": b"detector-bytes", "rec.onnx": b"recognizer-bytes"}
    archive = corrupt_crc(make_zip(members), "rec.onnx")
    s = FakeServer({"/arch.zip": archive}).start()
    try:
        spec = make_spec(s.base_url, members, archive=("arch.zip", archive))
        with pytest.raises(InstallError):
            install_model(settings, spec, Progress(), threading.Event())
        d = model_dir(settings, spec)
        assert not [p for p in d.rglob("*") if p.is_file()]
    finally:
        s.stop()


def test_network_error_message(settings: Settings, server: FakeServer) -> None:
    spec = spec_for(server)
    server.stop()
    with pytest.raises(InstallError, match="Check your connection"):
        install_model(settings, spec, Progress(), threading.Event())


def test_cancel_stops_and_keeps_partial(settings: Settings) -> None:
    big = bytes(3 * 1024 * 1024)
    s = FakeServer({"/big.bin": big}).start()
    try:
        spec = make_spec(s.base_url, {"big.bin": big})
        cancel = threading.Event()

        class CancelOnFirstChunk(Progress):
            def __setattr__(self, name: str, value: object) -> None:
                super().__setattr__(name, value)
                if name == "done" and value:
                    cancel.set()

        with pytest.raises(InstallCancelled):
            install_model(settings, spec, CancelOnFirstChunk(), cancel)
        d = model_dir(settings, spec)
        assert 0 < (d / "big.bin.partial").stat().st_size < len(big)
        assert not (d / "big.bin").exists()
    finally:
        s.stop()


def test_archive_model_extracts_only_listed_files(settings: Settings) -> None:
    members = {"det.onnx": b"detector", "rec.onnx": b"recognizer", "extra.onnx": b"ignored"}
    archive = make_zip(members)
    s = FakeServer({"/arch.zip": archive}).start()
    try:
        spec = make_spec(
            s.base_url,
            {"det.onnx": members["det.onnx"], "rec.onnx": members["rec.onnx"]},
            archive=("arch.zip", archive),
        )
        install_model(settings, spec, Progress(), threading.Event())
        d = model_dir(settings, spec)
        assert sorted(p.name for p in d.iterdir()) == ["det.onnx", "rec.onnx"]
        assert [r["path"] for r in s.requests] == ["/arch.zip"]
        assert is_installed(settings, spec)
    finally:
        s.stop()


def test_import_zip_verifies(settings: Settings, tmp_path: Path) -> None:
    spec = offline_spec()
    good = tmp_path / "good.zip"
    good.write_bytes(make_zip(FILES))
    import_zip(settings, spec, good)
    assert is_installed(settings, spec)
    assert good.exists()  # the user's zip is never deleted

    bad_settings = Settings(data_dir=tmp_path / "other")
    bad = tmp_path / "bad.zip"
    bad.write_bytes(make_zip({**FILES, "a.bin": b"tampered"}))
    with pytest.raises(InstallError):
        import_zip(bad_settings, spec, bad)
    assert not is_installed(bad_settings, spec)
    d = model_dir(bad_settings, spec)
    assert not d.exists() or not [p for p in d.rglob("*") if p.is_file()]
    assert bad.exists()


def test_import_zip_with_nested_paths(settings: Settings, tmp_path: Path) -> None:
    files = {"1_Pooling/config.json": b'{"p": 1}', "config.json": b"{}"}
    spec = make_spec("http://unused.invalid", files)
    z = tmp_path / "m.zip"
    z.write_bytes(make_zip({f"embeddinggemma-2/{p}": d for p, d in files.items()}))
    import_zip(settings, spec, z)
    assert (model_dir(settings, spec) / "1_Pooling" / "config.json").read_bytes() == b'{"p": 1}'


def test_zip_slip_rejected(settings: Settings, tmp_path: Path) -> None:
    spec = offline_spec()
    z = tmp_path / "evil.zip"
    z.write_bytes(make_zip({**FILES, "../evil": b"pwned"}))
    with pytest.raises(InstallError):
        import_zip(settings, spec, z)
    assert not list(tmp_path.rglob("evil"))
    assert not is_installed(settings, spec)


def test_record_install_and_read(settings: Settings) -> None:
    spec = offline_spec()
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert read_installed(settings) == {}
    record_install(settings, spec, when)
    entry = read_installed(settings)["m1"]
    assert entry["version"] == spec.files[0].sha256[:12]
    assert entry["files"] == {f.path: f.sha256 for f in spec.files}
    assert entry["license_accepted_at"] == when.isoformat()
    json.loads((settings.models_dir / "installed.json").read_text())


def test_import_zip_checks_free_space_for_extracted_files_only(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user's zip is already on disk: only the extracted files need room."""
    members = {"det.onnx": b"d" * 4000, "rec.onnx": b"r" * 6000}
    archive = make_zip(members)
    z = tmp_path / "user.zip"
    z.write_bytes(archive)
    spec = make_spec("http://unused", members, archive=("arch.zip", archive))
    files = sum(len(d) for d in members.values())

    def free(n: int) -> None:
        monkeypatch.setattr(
            "shutil.disk_usage", lambda p: SimpleNamespace(total=n * 10, used=0, free=n)
        )

    free(int(1.2 * files) - 1)
    with pytest.raises(InstallError, match="free space"):
        import_zip(settings, spec, z)
    assert not is_installed(settings, spec)
    free(int(1.2 * files) + 1)  # enough for the files, not files + archive
    import_zip(settings, spec, z)
    assert is_installed(settings, spec)
    assert z.read_bytes() == archive  # never modified
