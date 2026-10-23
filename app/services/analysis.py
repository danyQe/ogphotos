import logging
import queue
import threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import File
from app.services.embedder import Embedder, ModelUnavailableError
from app.services.grouping import Grouper
from app.services.sharpness import sharpness
from app.services.vector_index import VectorIndex, to_blob

log = logging.getLogger(__name__)

STATUSES = ("pending", "done", "failed")


def load_for_analysis(path: Path, max_side: int) -> Image.Image:
    """Decode once, upright (EXIF orientation), RGB, long edge ≤ max_side."""
    with Image.open(path) as img:
        img.draft("RGB", (max_side, max_side))
        upright = ImageOps.exif_transpose(img).convert("RGB")
    upright.thumbnail((max_side, max_side))
    return upright


def status_counts(session: Session) -> dict[str, int]:
    """Analysis progress over non-trashed images."""
    rows = session.execute(
        select(File.analysis_status, func.count())
        .where(File.media_type == "image", File.is_trashed.is_(False))
        .group_by(File.analysis_status)
    ).all()
    counts = dict.fromkeys(STATUSES, 0)
    counts.update({status: n for status, n in rows if status in counts})
    return counts


class AnalysisWorker:
    """Single background thread: embeds + scores `pending` images in batches.

    `start()` also queues every pending row, so analysis resumes after a restart;
    `resume_pending()` does the same once the model has been installed."""

    def __init__(
        self,
        factory: sessionmaker[Session],
        settings: Settings,
        embedder: Embedder,
        index: VectorIndex,
        grouper: Grouper | None = None,
    ) -> None:
        self.factory = factory
        self.settings = settings
        self.embedder = embedder
        self.index = index
        self.grouper = grouper
        self._queue: queue.Queue[int] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None  # model-level problem, shown in /api/status

    def start(self) -> None:
        self.resume_pending()
        self._thread = threading.Thread(target=self._run, name="analysis", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def resume_pending(self) -> int:
        """Queue every pending, non-trashed file (startup, or once the model is installed)."""
        with self.factory() as session:
            pending = session.scalars(
                select(File.id)
                .where(File.analysis_status == "pending", File.is_trashed.is_(False))
                .order_by(File.id)
            ).all()
        for file_id in pending:
            self.enqueue(file_id)
        return len(pending)

    def enqueue(self, file_id: int) -> None:
        self._queue.put(file_id)

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until everything queued so far is processed (tests, scripts)."""
        q = self._queue
        with q.all_tasks_done:
            return q.all_tasks_done.wait_for(lambda: q.unfinished_tasks == 0, timeout)

    def _run(self) -> None:
        # Group writes from here and from review actions serialize on grouper.lock.
        if self.grouper is not None:
            try:
                self.grouper.backfill(self.factory)
            except Exception:
                log.exception("grouping backfill failed")
        while not self._stop.is_set():
            try:
                batch = [self._queue.get(timeout=0.5)]
            except queue.Empty:
                continue
            while len(batch) < self.settings.embed_batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            try:
                self.process(batch)
                self.error = None
            except ModelUnavailableError as e:
                # Files stay `pending`; resume_pending() re-queues them at startup
                # and when the model is installed from /setup/models.
                log.error("%s", e)
                self.error = str(e)
            except Exception:
                log.exception("analysis batch failed: %s", batch)
            finally:
                for _ in batch:
                    self._queue.task_done()

    def process(self, file_ids: list[int]) -> None:
        with self.factory() as session:
            rows = session.scalars(
                select(File).where(
                    File.id.in_(set(file_ids)),
                    File.analysis_status == "pending",
                    File.is_trashed.is_(False),
                )
            ).all()
            loaded: list[tuple[File, Image.Image]] = []
            for row in rows:
                try:
                    img = load_for_analysis(
                        self.settings.data_dir / row.stored_path, self.settings.analysis_max_side
                    )
                    row.sharpness = sharpness(img, self.settings.sharpness_max_side)
                    loaded.append((row, img))
                except Exception as e:
                    self._fail(row, f"could not read image: {e}")
            done = [(row, vec) for row, vec in self._embed(loaded) if vec is not None]
            for row, vec in done:
                row.embedding = to_blob(vec)
                row.analysis_status = "done"
                row.analysis_error = None
            session.commit()
        for row, vec in done:
            self.index.add(row.id, vec)
        if self.grouper is not None and done:
            self._group([row.id for row, _ in done])

    def _group(self, file_ids: list[int]) -> None:
        """Place newly analyzed files into groups, oldest first. Failures don't fail files."""
        try:
            with self.grouper.lock, self.factory() as session:
                ordered = session.scalars(
                    select(File.id).where(File.id.in_(file_ids)).order_by(File.taken_at, File.id)
                ).all()
                for file_id in ordered:
                    self.grouper.assign(session, file_id)
                session.commit()
        except Exception:
            log.exception("grouping failed for %s", file_ids)

    def _embed(
        self, loaded: list[tuple[File, Image.Image]]
    ) -> list[tuple[File, np.ndarray | None]]:
        """Embed a batch; if the batch call fails, retry one by one to isolate the bad file."""
        if not loaded:
            return []
        try:
            vecs = self.embedder.embed_images([img for _, img in loaded])
        except ModelUnavailableError:
            raise
        except Exception as e:
            if len(loaded) == 1:
                self._fail(loaded[0][0], f"embedding failed: {e}")
                return [(loaded[0][0], None)]
            return [pair for item in loaded for pair in self._embed([item])]
        out: list[tuple[File, np.ndarray | None]] = []
        for (row, _), vec in zip(loaded, vecs, strict=True):
            if not np.isfinite(vec).all():
                log.error("NaN/inf embedding for file %s — check model dtype", row.id)
                self._fail(row, "embedding contains NaN/inf")
                out.append((row, None))
            else:
                out.append((row, vec))
        return out

    @staticmethod
    def _fail(row: File, message: str) -> None:
        log.warning("analysis failed for file %s: %s", row.id, message)
        row.analysis_status = "failed"
        row.analysis_error = message
