"""Resumable, checksum-verified model downloads (the only network use in OGphotos).

Only our own `.partial` files and our own downloaded archive are ever deleted here;
`import_zip` never touches the user's zip.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import shutil
import threading
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.models_registry import REGISTRY, ModelFile, ModelSpec, get_spec

log = logging.getLogger(__name__)

CHUNK = 1 << 20
SPACE_FACTOR = 1.2  # require this multiple of the download size to be free
TIMEOUT_S = 30
INSTALLED_FILE = "installed.json"
CORRUPT_MSG = "Download corrupted — retry"
BAD_ZIP_MSG = "The zip is corrupted or is not the expected model (checksum mismatch)"


class InstallError(RuntimeError):
    """Message is shown to the user."""


class InstallCancelled(InstallError):
    """The caller set the cancel event; partial downloads are kept for resuming."""


@dataclass
class Progress:
    done: int = 0
    total: int = 0
    bytes_per_s: float = 0.0


def model_dir(settings: Settings, spec: ModelSpec) -> Path:
    return settings.models_dir / spec.id


def is_installed(settings: Settings, spec: ModelSpec) -> bool:
    """Every file present at its expected size (sha256 is checked at install time)."""
    d = model_dir(settings, spec)
    return all((d / f.path).is_file() and (d / f.path).stat().st_size == f.size for f in spec.files)


def read_installed(settings: Settings) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads((settings.models_dir / INSTALLED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_install(settings: Settings, spec: ModelSpec, accepted_at: datetime) -> None:
    ref = spec.archive.sha256 if spec.archive else spec.files[0].sha256
    entries = read_installed(settings)
    entries[spec.id] = {
        "version": ref[:12],
        "files": {f.path: f.sha256 for f in spec.files},
        "license_accepted_at": accepted_at.isoformat(),
    }
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    path = settings.models_dir / INSTALLED_FILE
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    tmp.replace(path)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _peak_bytes(spec: ModelSpec) -> int:
    """Peak disk use: an archive and its extracted files coexist briefly."""
    files = sum(f.size for f in spec.files)
    return files + spec.archive.size if spec.archive else files


def _check_disk_space(settings: Settings, spec: ModelSpec, *, have_archive: bool = False) -> None:
    """`have_archive`: the zip is already on disk (an import), so only the files need room."""
    probe = settings.models_dir
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    peak = sum(f.size for f in spec.files) if have_archive else _peak_bytes(spec)
    need = int(SPACE_FACTOR * peak)
    if shutil.disk_usage(probe).free < need:
        raise InstallError(f"Not enough free space: need {need / 1e9:.1f} GB")


class _Rate:
    def __init__(self, progress: Progress) -> None:
        self._progress = progress
        self._t0 = time.monotonic()
        self._bytes = 0

    def add(self, n: int) -> None:
        self._bytes += n
        self._progress.done += n
        self._progress.bytes_per_s = self._bytes / max(time.monotonic() - self._t0, 1e-6)


def _reason(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}"
    if isinstance(e, urllib.error.URLError):
        return str(e.reason)
    return str(e) or type(e).__name__


def _fetch(
    file: ModelFile,
    partial: Path,
    progress: Progress,
    rate: _Rate,
    cancel: threading.Event,
    opener: Callable[..., Any],
) -> None:
    """Download `file` into `partial` (resuming if possible) and verify its sha256."""
    partial.parent.mkdir(parents=True, exist_ok=True)
    have = partial.stat().st_size if partial.exists() else 0
    if have > file.size:
        partial.unlink()
        have = 0
    progress.done += have
    if have < file.size:
        headers = {"Range": f"bytes={have}-"} if have else {}
        req = urllib.request.Request(file.url, headers=headers)
        try:
            with opener(req, timeout=TIMEOUT_S) as resp:
                if have and resp.status == 206:
                    mode = "ab"
                else:  # fresh download, or the server ignored Range and sent everything
                    progress.done -= have
                    mode = "wb"
                with partial.open(mode) as out:
                    while True:
                        if cancel.is_set():
                            raise InstallCancelled("Download cancelled")
                        try:
                            chunk = resp.read(CHUNK)
                        except http.client.IncompleteRead as e:
                            out.write(e.partial)
                            rate.add(len(e.partial))
                            raise
                        if not chunk:
                            break
                        out.write(chunk)
                        rate.add(len(chunk))
        except (OSError, http.client.HTTPException) as e:
            raise InstallError(
                f"Download failed: {_reason(e)}. Check your connection and retry."
            ) from e
    if partial.stat().st_size < file.size:  # connection dropped early; keep it for resuming
        raise InstallError(
            "Download failed: connection closed early. Check your connection and retry."
        )
    if partial.stat().st_size != file.size or _sha256(partial) != file.sha256:
        partial.unlink()
        raise InstallError(CORRUPT_MSG)


def _find_member(zf: zipfile.ZipFile, path: str) -> zipfile.ZipInfo | None:
    """Member named `path`, or the one ending in `/path` with the shortest prefix."""
    best: tuple[int, zipfile.ZipInfo] | None = None
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if info.is_dir() or not (name == path or name.endswith("/" + path)):
            continue
        if best is None or len(name) < best[0]:
            best = (len(name), info)
    return best[1] if best else None


def _extract_verified(spec: ModelSpec, zip_path: Path, dest: Path, bad_msg: str) -> None:
    """Extract only `spec.files` from the zip into `dest`; install all or nothing."""
    root = dest.resolve()
    staged: list[tuple[Path, Path]] = []
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise InstallError(f"Could not read the zip file: {e}") from e
    with zf:
        for info in zf.infolist():  # zip-slip: no member may resolve outside the model dir
            if not (root / info.filename).resolve().is_relative_to(root):
                raise InstallError("The zip contains an unsafe path and was rejected")
        try:
            for f in spec.files:
                member = _find_member(zf, f.path)
                if member is None:
                    raise InstallError(f"The zip is missing {f.path}")
                tmp = root / (f.path + ".partial")
                tmp.parent.mkdir(parents=True, exist_ok=True)
                staged.append((tmp, root / f.path))
                h = hashlib.sha256()
                with zf.open(member) as src, tmp.open("wb") as out:
                    while chunk := src.read(CHUNK):
                        h.update(chunk)
                        out.write(chunk)
                if h.hexdigest() != f.sha256:
                    raise InstallError(bad_msg)
            for tmp, final in staged:
                tmp.replace(final)
        except Exception as e:
            for tmp, _ in staged:
                tmp.unlink(missing_ok=True)
            if isinstance(e, InstallError):
                raise
            if isinstance(e, (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError)):
                raise InstallError(bad_msg) from e
            if isinstance(e, OSError):
                raise InstallError(
                    f"Couldn't write the model files: {_reason(e)}. Check free disk space."
                ) from e
            raise


def install_model(
    settings: Settings,
    spec: ModelSpec,
    progress: Progress,
    cancel: threading.Event,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> None:
    progress.total = spec.total_size
    progress.done = 0
    d = model_dir(settings, spec)
    if is_installed(settings, spec):
        progress.done = progress.total
        return
    _check_disk_space(settings, spec)
    d.mkdir(parents=True, exist_ok=True)
    rate = _Rate(progress)
    if spec.archive:
        partial = d / (spec.archive.path + ".partial")
        _fetch(spec.archive, partial, progress, rate, cancel, opener)
        try:
            _extract_verified(spec, partial, d, CORRUPT_MSG)
        finally:
            partial.unlink(missing_ok=True)  # our own download, not a user file
        return
    for f in spec.files:
        final = d / f.path
        if final.is_file() and final.stat().st_size == f.size:
            progress.done += f.size  # verified when it was installed
            continue
        partial = final.with_name(final.name + ".partial")
        _fetch(f, partial, progress, rate, cancel, opener)
        partial.replace(final)


def import_zip(settings: Settings, spec: ModelSpec, zip_path: Path) -> None:
    """Install from a zip the user already downloaded. The zip itself is never modified."""
    _check_disk_space(settings, spec, have_archive=True)
    d = model_dir(settings, spec)
    d.mkdir(parents=True, exist_ok=True)
    _extract_verified(spec, zip_path, d, BAD_ZIP_MSG)


@dataclass
class _Job:
    progress: Progress
    cancel: threading.Event
    running: bool = True
    error: str | None = None
    thread: threading.Thread | None = None


class ModelInstaller:
    """Background install jobs, one thread per model, started only by the user (Download).

    A cancelled job reports "missing" with its progress kept, so the UI can offer Resume."""

    def __init__(self, settings: Settings, on_installed: Callable[[str], None]) -> None:
        self.settings = settings
        self.on_installed = on_installed
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()

    def start(self, model_id: str, accepted_at: datetime | None = None) -> None:
        spec = get_spec(model_id)
        accepted = accepted_at or datetime.now(UTC)
        with self._lock:
            if self._running(model_id):
                raise InstallError(f"{spec.name} is already downloading")
            job = _Job(Progress(total=spec.total_size), threading.Event())
            job.thread = threading.Thread(
                target=self._run,
                args=(spec, job, accepted),
                name=f"install-{model_id}",
                daemon=True,
            )
            self._jobs[model_id] = job
        job.thread.start()

    def cancel(self, model_id: str) -> None:
        with self._lock:
            job = self._jobs.get(model_id)
        if job is not None:
            job.cancel.set()

    def import_zip(
        self, model_id: str, zip_path: Path, accepted_at: datetime | None = None
    ) -> None:
        spec = get_spec(model_id)
        with self._lock:
            if self._running(model_id):
                raise InstallError(f"{spec.name} is already downloading")
        import_zip(self.settings, spec, zip_path)
        self._installed(spec, accepted_at or datetime.now(UTC))
        with self._lock:
            self._jobs.pop(model_id, None)

    def stop(self, timeout: float = 5.0) -> None:
        """Cancel running downloads (partials are kept for resuming) and wait briefly."""
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel.set()
        for job in jobs:
            if job.thread is not None:
                job.thread.join(timeout)

    def state(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        with self._lock:
            jobs = dict(self._jobs)
        for model_id, spec in REGISTRY.items():
            job = jobs.get(model_id)
            entry: dict[str, Any] = {
                "status": "missing",
                "done": job.progress.done if job else 0,
                "total": (job.progress.total if job else 0) or spec.total_size,
                "bytes_per_s": job.progress.bytes_per_s if job else 0.0,
                "error": None,
            }
            if job is not None and job.running:
                entry["status"] = "downloading"
            elif is_installed(self.settings, spec):
                entry.update(status="installed", done=spec.total_size, total=spec.total_size)
            elif job is not None and job.error:
                entry.update(status="error", error=job.error)
            out[model_id] = entry
        return out

    def _running(self, model_id: str) -> bool:
        job = self._jobs.get(model_id)
        return job is not None and job.running

    def _installed(self, spec: ModelSpec, accepted_at: datetime) -> None:
        record_install(self.settings, spec, accepted_at)
        try:
            self.on_installed(spec.id)
        except Exception:
            log.exception("on_installed hook failed for %s", spec.id)

    def _run(self, spec: ModelSpec, job: _Job, accepted_at: datetime) -> None:
        try:
            install_model(self.settings, spec, job.progress, job.cancel)
            self._installed(spec, accepted_at)
        except InstallCancelled:
            log.info("install of %s cancelled", spec.id)
        except InstallError as e:
            job.error = str(e)
        except Exception as e:
            log.exception("install of %s failed", spec.id)
            job.error = f"Install failed: {_reason(e)}"
        finally:
            job.running = False
