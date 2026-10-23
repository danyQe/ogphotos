"""Server-side folder import (PRD 5.2): walk a local folder and ingest every file in it.

Source files are only ever opened read-only; ingest copies them into the library."""

import logging
import os
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.services.ingest import IngestResult, ingest_file
from app.services.phash_index import PhashIndex
from app.services.sidecar import find_sidecar

log = logging.getLogger(__name__)

SIDECAR_MAX_BYTES = 1024 * 1024
ANALYZE_STATUSES = {"new", "similar_exists"}


class ImportRejected(ValueError):
    """Bad path or a job already running (message is shown to the user)."""


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def scan(root: Path) -> list[tuple[Path, list[str]]]:
    """Directories under `root` (sorted, hidden ones skipped) with their file names."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        names = sorted(n for n in filenames if not n.startswith("."))
        if names:
            out.append((Path(dirpath), names))
    return out


def _read_sidecars(root: Path, directory: Path, names: list[str]) -> dict[str, bytes]:
    out = {}
    for n in names:
        if n.lower().endswith(".json"):
            try:
                with (directory / n).open("rb") as fh:
                    out[(directory / n).relative_to(root).as_posix()] = fh.read(SIDECAR_MAX_BYTES)
            except OSError as e:
                log.warning("could not read sidecar %s: %s", directory / n, e)
    return out


@dataclass
class ImportJob:
    id: str
    path: str
    status: str = "running"  # running | done | cancelled | failed
    total: int = 0
    processed: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    started_at: datetime = field(default_factory=_utcnow)
    finished_at: datetime | None = None

    def snapshot(self, offset: int = 0) -> dict[str, Any]:
        """JSON view; `results` from `offset` on so pollers fetch only new rows."""
        data = asdict(self)
        data["results"] = self.results[offset:]
        data["offset"] = offset
        return data


class Importer:
    """Runs one folder import at a time on a background thread."""

    def __init__(
        self,
        factory: sessionmaker[Session],
        settings: Settings,
        phash_index: PhashIndex,
        enqueue: Callable[[int], None],
    ) -> None:
        self.factory = factory
        self.settings = settings
        self.phash_index = phash_index
        self.enqueue = enqueue
        self.jobs: dict[str, ImportJob] = {}
        self._lock = threading.Lock()
        self._cancel: dict[str, threading.Event] = {}
        self._threads: list[threading.Thread] = []

    def validate(self, raw: str) -> Path:
        path = Path(raw.strip().strip('"')).expanduser()
        if not raw.strip() or not path.is_absolute():
            raise ImportRejected("enter an absolute folder path")
        if not path.is_dir():
            raise ImportRejected(f"not a folder: {path}")
        root, data = path.resolve(), self.settings.data_dir.resolve()
        if root == data or root.is_relative_to(data) or data.is_relative_to(root):
            raise ImportRejected("that folder contains (or is inside) the OGphotos data folder")
        return root

    def start(self, raw_path: str) -> ImportJob:
        root = self.validate(raw_path)
        with self._lock:
            if any(j.status == "running" for j in self.jobs.values()):
                raise ImportRejected("an import is already running")
            job = ImportJob(id=uuid4().hex[:12], path=str(root))
            self.jobs[job.id] = job
            self._cancel[job.id] = threading.Event()
        t = threading.Thread(target=self._run, args=(job, root), name="import", daemon=True)
        self._threads.append(t)
        t.start()
        return job

    def cancel(self, job_id: str) -> None:
        if event := self._cancel.get(job_id):
            event.set()

    def stop(self) -> None:
        for event in self._cancel.values():
            event.set()
        for t in self._threads:
            t.join()

    def wait(self, job_id: str, timeout: float | None = None) -> bool:
        """Block until the job's thread ends (tests, scripts)."""
        for t in list(self._threads):
            t.join(timeout)
        return self.jobs[job_id].status != "running"

    def _run(self, job: ImportJob, root: Path) -> None:
        cancel = self._cancel[job.id]
        counts: Counter[str] = Counter()
        try:
            dirs = scan(root)
            job.total = sum(
                1 for _, names in dirs for n in names if not n.lower().endswith(".json")
            )
            with self.factory() as session:
                for directory, names in dirs:
                    sidecars = _read_sidecars(root, directory, names)
                    for name in names:
                        if name.lower().endswith(".json"):
                            continue
                        if cancel.is_set():
                            job.status = "cancelled"
                            return
                        result = self._one(session, root, directory / name, sidecars)
                        counts[result["status"]] += 1
                        if result.get("date_fixed"):
                            counts["date_fixed"] += 1
                        job.counts = dict(counts)
                        job.results.append(result)
                        job.processed += 1
            job.status = "done"
        except Exception as e:
            log.exception("folder import failed: %s", root)
            job.status, job.error = "failed", str(e)
        finally:
            job.finished_at = _utcnow()

    def _one(
        self, session: Session, root: Path, path: Path, sidecars: dict[str, bytes]
    ) -> dict[str, Any]:
        rel = path.relative_to(root).as_posix()
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).replace(tzinfo=None)
            with path.open("rb") as fh:  # read-only: the source is never modified
                result = ingest_file(
                    session,
                    self.settings,
                    fh,
                    rel,
                    sidecar=find_sidecar(rel, sidecars),
                    mtime=mtime,
                    phash_index=self.phash_index,
                )
        except OSError as e:
            result = IngestResult(rel, "error", message=f"could not read: {e}")
        if result.status in ANALYZE_STATUSES and result.file_id is not None:
            self.enqueue(result.file_id)
        return asdict(result)
