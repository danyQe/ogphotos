"""Assemble the download payload: `python packaging/stage.py --target {windows|macos|linux}
--out DIR` writes DIR/ogphotos/ with the app source, uv.lock, licenses, the launch script,
the pinned uv binary for each supported CPU (bin/<arch>/uv[.exe]) and the icons.

uv comes from the official GitHub release named in uv-version.txt and is checked against
uv-sha256.txt before anything is extracted. The release archives hold only the binaries, so
uv's license texts (MIT OR Apache-2.0, from the same uv tag) are vendored in uv-licenses/
and copied to bin/ next to the binaries."""

import argparse
import hashlib
import io
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

import make_icons

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PAYLOAD = "ogphotos"
UV_URL = "https://github.com/astral-sh/uv/releases/download/{version}/{asset}"
DOWNLOAD_TIMEOUT_S = 120

# target -> [(bin/<arch> folder, release asset)]; macOS is arm64-only (see the release spec)
TARGETS: dict[str, list[tuple[str, str]]] = {
    "windows": [("x86_64", "uv-x86_64-pc-windows-msvc.zip")],
    "macos": [("aarch64", "uv-aarch64-apple-darwin.tar.gz")],
    "linux": [
        ("x86_64", "uv-x86_64-unknown-linux-gnu.tar.gz"),
        ("aarch64", "uv-aarch64-unknown-linux-gnu.tar.gz"),
    ],
}
LAUNCH_SCRIPTS = {"windows": ["launch.cmd"], "macos": ["launch.sh"], "linux": ["launch.sh"]}
ROOT_FILES = ["pyproject.toml", "uv.lock", "LICENSE", "THIRD_PARTY_LICENSES.md"]
UV_LICENSES = ["LICENSE-MIT", "LICENSE-APACHE"]  # packaging/uv-licenses/, from the uv tag
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")

Fetch = Callable[[str], bytes]


class ChecksumError(RuntimeError):
    pass


def uv_version() -> str:
    return (HERE / "uv-version.txt").read_text(encoding="utf-8").strip()


def parse_checksums(text: str) -> dict[str, str]:
    """`<sha256>  <file>` lines (sha256sum format; a `*` binary marker is allowed)."""
    sums = {}
    for line in text.splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            sums[name.strip().lstrip("*")] = digest.lower()
    return sums


def load_checksums() -> dict[str, str]:
    return parse_checksums((HERE / "uv-sha256.txt").read_text(encoding="utf-8"))


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as resp:
        return resp.read()


def verified(asset: str, data: bytes, checksums: dict[str, str]) -> bytes:
    expected = checksums.get(asset)
    if expected is None:
        raise ChecksumError(f"{asset} is not listed in uv-sha256.txt")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ChecksumError(f"{asset}: sha256 {actual} does not match pinned {expected}")
    return data


def extract_uv(asset: str, data: bytes) -> bytes:
    """The `uv` binary from a release archive (uvx/uvw are not shipped)."""
    name = "uv.exe" if asset.endswith(".zip") else "uv"
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = [m for m in zf.namelist() if m.rsplit("/", 1)[-1] == name]
            if members:
                return zf.read(members[0])
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            for member in tf.getmembers():
                if member.isfile() and member.name.rsplit("/", 1)[-1] == name:
                    fh = tf.extractfile(member)
                    if fh is not None:
                        return fh.read()
    raise RuntimeError(f"{name} not found in {asset}")


def stage(
    target: str,
    out: Path,
    fetch: Fetch = download,
    checksums: dict[str, str] | None = None,
) -> Path:
    """Build out/ogphotos for `target` (an existing out/ogphotos is replaced). Every uv
    download is verified before the payload folder is touched."""
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}")
    checksums = load_checksums() if checksums is None else checksums
    version = uv_version()
    binaries = []
    for arch, asset in TARGETS[target]:
        data = verified(asset, fetch(UV_URL.format(version=version, asset=asset)), checksums)
        binaries.append((arch, asset, extract_uv(asset, data)))

    dest = out / PAYLOAD
    if dest.exists():
        shutil.rmtree(dest)  # a previous build output, never user data
    dest.mkdir(parents=True)
    shutil.copytree(ROOT / "app", dest / "app", ignore=SKIP)
    for name in ROOT_FILES:
        shutil.copy2(ROOT / name, dest / name)
    for name in LAUNCH_SCRIPTS[target]:
        shutil.copy2(HERE / name, dest / name)
        (dest / name).chmod(0o755)
    for arch, asset, binary in binaries:
        path = dest / "bin" / arch / ("uv.exe" if asset.endswith(".zip") else "uv")
        path.parent.mkdir(parents=True)
        path.write_bytes(binary)
        path.chmod(0o755)
    for name in UV_LICENSES:
        shutil.copy2(HERE / "uv-licenses" / name, dest / "bin" / name)
    make_icons.make_icons(dest / "icons")
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        dest = stage(args.target, args.out)
    except (ChecksumError, OSError, RuntimeError) as e:
        print(f"stage: {e}", file=sys.stderr)
        return 1
    print(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
