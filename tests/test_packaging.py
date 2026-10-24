import hashlib
import io
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))

import make_icons  # noqa: E402
import stage  # noqa: E402

FAKE_UV = b"#!fake uv binary\n"


def _archive(name: str) -> bytes:
    """An archive shaped like the real uv release asset for `name`."""
    buf = io.BytesIO()
    if name.endswith(".zip"):
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("uv.exe", FAKE_UV)
            zf.writestr("uvx.exe", b"uvx")
    else:
        triple = name.removeprefix("uv-").removesuffix(".tar.gz")
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for member, data in ((f"uv-{triple}/uv", FAKE_UV), (f"uv-{triple}/uvx", b"uvx")):
                info = tarfile.TarInfo(member)
                info.size = len(data)
                info.mode = 0o755
                tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeDownload:
    """Stands in for the GitHub download; records the URLs asked for."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.assets = {a: _archive(a) for t in stage.TARGETS.values() for _, a in t}
        self.checksums = {a: hashlib.sha256(d).hexdigest() for a, d in self.assets.items()}

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        return self.assets[url.rsplit("/", 1)[1]]


@pytest.fixture
def fake_uv_download() -> FakeDownload:
    return FakeDownload()


def _stage(target: str, out: Path, fake: FakeDownload, checksums: dict | None = None) -> Path:
    return stage.stage(target, out, fetch=fake, checksums=checksums or fake.checksums)


def test_stage_layout_linux(tmp_path: Path, fake_uv_download: FakeDownload) -> None:
    dest = _stage("linux", tmp_path, fake_uv_download)
    assert dest == tmp_path / "ogphotos"
    for rel in (
        "app/__init__.py",
        "app/launcher.py",
        "app/static/style.css",
        "pyproject.toml",
        "uv.lock",
        "LICENSE",
        "THIRD_PARTY_LICENSES.md",
        "launch.sh",
        "bin/x86_64/uv",
        "bin/aarch64/uv",
        "bin/LICENSE-MIT",
        "bin/LICENSE-APACHE",
        "icons/ogphotos.png",
        "icons/ogphotos.ico",
        "icons/ogphotos.icns",
    ):
        assert (dest / rel).is_file(), rel
    assert (dest / "bin/x86_64/uv").read_bytes() == FAKE_UV
    assert not (dest / "launch.cmd").exists()
    assert not list(dest.rglob("uvx*"))
    for absent in ("tests", "docs", ".git", "data", "scripts", "packaging", ".venv"):
        assert not (dest / absent).exists(), absent
    assert not list(dest.rglob("__pycache__")) and not list(dest.rglob("*.pyc"))
    ver = stage.uv_version()
    assert fake_uv_download.urls == [
        f"https://github.com/astral-sh/uv/releases/download/{ver}/uv-x86_64-unknown-linux-gnu.tar.gz",
        f"https://github.com/astral-sh/uv/releases/download/{ver}/uv-aarch64-unknown-linux-gnu.tar.gz",
    ]


def test_stage_layout_windows_and_macos(tmp_path: Path, fake_uv_download: FakeDownload) -> None:
    win = _stage("windows", tmp_path / "w", fake_uv_download)
    assert (win / "launch.cmd").is_file() and not (win / "launch.sh").exists()
    assert (win / "bin/x86_64/uv.exe").read_bytes() == FAKE_UV
    assert [p.name for p in (win / "bin").iterdir() if p.is_dir()] == ["x86_64"]
    mac = _stage("macos", tmp_path / "m", fake_uv_download)
    assert (mac / "launch.sh").is_file() and (mac / "bin/aarch64/uv").is_file()
    assert [p.name for p in (mac / "bin").iterdir() if p.is_dir()] == ["aarch64"]
    for d in (win, mac):
        assert "MIT License" in (d / "bin/LICENSE-MIT").read_text(encoding="utf-8")
        assert "Apache License" in (d / "bin/LICENSE-APACHE").read_text(encoding="utf-8")


def test_stage_replaces_previous_output(tmp_path: Path, fake_uv_download: FakeDownload) -> None:
    stale = tmp_path / "ogphotos" / "stale.txt"
    stale.parent.mkdir()
    stale.write_text("old")
    _stage("linux", tmp_path, fake_uv_download)
    assert not stale.exists()


def test_stage_rejects_bad_uv_checksum(tmp_path: Path, fake_uv_download: FakeDownload) -> None:
    bad = {a: "0" * 64 for a in fake_uv_download.checksums}
    with pytest.raises(stage.ChecksumError):
        _stage("linux", tmp_path, fake_uv_download, checksums=bad)
    assert not list((tmp_path / "ogphotos").rglob("uv"))


def test_stage_rejects_asset_missing_from_checksums(
    tmp_path: Path, fake_uv_download: FakeDownload
) -> None:
    with pytest.raises(stage.ChecksumError):
        _stage("windows", tmp_path, fake_uv_download, checksums={"other.zip": "0" * 64})


def test_pinned_uv_files_cover_every_asset() -> None:
    assert stage.uv_version() == "0.10.10"
    sums = stage.load_checksums()
    assets = {a for t in stage.TARGETS.values() for _, a in t}
    assert set(sums) == assets
    assert all(len(v) == 64 and int(v, 16) >= 0 for v in sums.values())


def test_parse_checksums_accepts_binary_marker() -> None:
    text = "ab" * 32 + " *uv-x.zip\n" + "cd" * 32 + "  uv-y.tar.gz\n\n"
    assert stage.parse_checksums(text) == {"uv-x.zip": "ab" * 32, "uv-y.tar.gz": "cd" * 32}


def test_launch_scripts_set_uv_env() -> None:
    for name in ("launch.sh", "launch.cmd"):
        text = (ROOT / "packaging" / name).read_text(encoding="utf-8")
        for var in (
            "UV_PYTHON_INSTALL_DIR",
            "UV_PROJECT_ENVIRONMENT",
            "UV_CACHE_DIR",
            "UV_PYTHON_PREFERENCE",
            "PYTHONPATH",
        ):
            assert var in text, (name, var)
        assert "--frozen" in text and "--no-default-groups --group" in text
        assert ".lock-hash" in text
        assert "Setting up OGphotos (one-time download of about 1 GB; usually 5–30 min)…" in text
        assert "Setting up OGphotos for your NVIDIA GPU (one-time download of about 4 GB" in text
        # GPU builds only where they can run; OGPHOTOS_TORCH overrides the detection
        assert "nvidia-smi --query-gpu" in text  # launch.cmd escapes "=" as "^="
        assert "driver_version" in text and "compute_cap" in text
        assert "580" in text and "75" in text and "OGPHOTOS_TORCH" in text
        assert "check your internet connection and re-run" in text
        assert "OGPHOTOS_NONINTERACTIVE" in text
        assert "-m app.launcher" in text
        assert "couldn't start. Delete the folder" in text and "run again to repair." in text


def test_launch_cmd_uses_crlf() -> None:
    data = (ROOT / "packaging" / "launch.cmd").read_bytes()
    assert data.count(b"\n") == data.count(b"\r\n")


def test_launch_cmd_repair_hint_only_for_codes_from_2() -> None:
    # signed ">= 2": a Ctrl+C stop (negative STATUS_CONTROL_C_EXIT) must not suggest a repair
    text = (ROOT / "packaging" / "launch.cmd").read_text(encoding="utf-8")
    assert "if %RC% GEQ 2 goto :start_failed" in text
    assert '"%RC%"==' not in text


def test_runtime_groups_match_launchers() -> None:
    # the launcher's group names must exist and conflict, or `uv sync --group` fails
    conf = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    groups = conf["dependency-groups"]
    assert {"cpu", "cuda"} <= groups.keys()
    assert conf["tool"]["uv"]["conflicts"] == [[{"group": "cpu"}, {"group": "cuda"}]]
    assert "cpu" in conf["tool"]["uv"]["default-groups"]
    assert not any(d.startswith(("torch", "onnxruntime")) for d in conf["project"]["dependencies"])


def test_launch_sh_uses_lf() -> None:
    assert b"\r" not in (ROOT / "packaging" / "launch.sh").read_bytes()


def test_make_icons_outputs(tmp_path: Path) -> None:
    make_icons.make_icons(tmp_path)
    png, ico, icns = (tmp_path / f"ogphotos.{ext}" for ext in ("png", "ico", "icns"))
    for path in (png, ico, icns):
        assert path.is_file() and path.stat().st_size > 0
    with Image.open(png) as im:
        assert im.size == (512, 512)
    with Image.open(ico) as im:
        assert im.format == "ICO"
        assert (256, 256) in im.info["sizes"] and (16, 16) in im.info["sizes"]
    with Image.open(icns) as im:
        assert im.format == "ICNS"


def test_icon_uses_style_css_accent(tmp_path: Path) -> None:
    make_icons.make_icons(tmp_path)
    accent = make_icons.css_color("--accent")
    assert accent == "#2f6fde"
    with Image.open(tmp_path / "ogphotos.png") as im:
        rgba = im.convert("RGBA")
        assert rgba.getpixel((0, 0))[3] == 0  # rounded corner is transparent
        r, g, b, a = rgba.getpixel((40, 256))  # left edge, clear of the text
        assert a == 255 and f"#{r:02x}{g:02x}{b:02x}" == accent
