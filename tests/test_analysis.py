from io import BytesIO

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import File
from app.services.analysis import AnalysisWorker, status_counts
from app.services.ingest import ingest_file
from app.services.vector_index import VectorIndex, from_blob
from tests.fakes import FakeEmbedder
from tests.imgutil import mp4_bytes, photo_bytes


@pytest.fixture
def embedder(settings: Settings) -> FakeEmbedder:
    return FakeEmbedder(settings.embed_dim)


def _make_worker(db, settings: Settings, embedder: FakeEmbedder) -> AnalysisWorker:
    return AnalysisWorker(db, settings, embedder, VectorIndex(settings.embed_dim))


def _add(db: sessionmaker[Session], settings: Settings, data: bytes, name: str) -> int:
    with db() as s:
        return ingest_file(s, settings, BytesIO(data), name).file_id


def _rows(db: sessionmaker[Session]) -> dict[str, File]:
    with db() as s:
        return {f.original_name: f for f in s.scalars(select(File))}


def _run(worker: AnalysisWorker) -> None:
    worker.start()
    try:
        assert worker.wait_idle(timeout=30)
    finally:
        worker.stop()


def test_pending_images_analyzed_on_start(db, settings: Settings, embedder) -> None:
    small = settings.model_copy(update={"embed_batch_size": 2})
    for i in range(5):
        _add(db, small, photo_bytes(i), f"{i}.jpg")
    _add(db, small, mp4_bytes(), "v.mp4")
    worker = _make_worker(db, small, embedder)
    _run(worker)

    rows = _rows(db)
    for i in range(5):
        row = rows[f"{i}.jpg"]
        assert row.analysis_status == "done" and row.analysis_error is None
        vec = from_blob(row.embedding)
        assert vec.shape == (settings.embed_dim,)
        assert np.linalg.norm(vec) == pytest.approx(1.0, abs=1e-4)
        assert row.sharpness is not None and row.sharpness > 0
    assert rows["v.mp4"].analysis_status == "skipped" and rows["v.mp4"].embedding is None
    assert len(worker.index) == 5
    assert max(embedder.calls) <= 2 and sum(embedder.calls) == 5
    with db() as s:
        assert status_counts(s) == {"pending": 0, "done": 5, "failed": 0}


def test_enqueued_after_start_and_resized_copy_is_neighbor(db, settings, embedder) -> None:
    worker = _make_worker(db, settings, embedder)
    worker.start()
    try:
        a = _add(db, settings, photo_bytes(1, size=(1600, 1200)), "a.jpg")
        b = _add(db, settings, photo_bytes(1, size=(400, 300)), "b.jpg")
        c = _add(db, settings, photo_bytes(2), "c.jpg")
        for file_id in (a, b, c, a):  # duplicate enqueue is harmless
            worker.enqueue(file_id)
        assert worker.wait_idle(timeout=30)
    finally:
        worker.stop()
    hits = worker.index.search(worker.index.get(a), 0.9, 5, exclude_id=a)
    assert [i for i, _ in hits] == [b]
    assert sum(embedder.calls) == 3


def test_nan_embedding_marks_failed(db, settings: Settings, embedder) -> None:
    embedder.nan_widths = {321}
    _add(db, settings, photo_bytes(1, size=(321, 240)), "nan.jpg")
    _add(db, settings, photo_bytes(2), "ok.jpg")
    worker = _make_worker(db, settings, embedder)
    _run(worker)
    rows = _rows(db)
    assert rows["nan.jpg"].analysis_status == "failed"
    assert "NaN" in rows["nan.jpg"].analysis_error
    assert rows["nan.jpg"].embedding is None
    assert rows["ok.jpg"].analysis_status == "done"
    assert len(worker.index) == 1


def test_unreadable_file_and_model_errors_mark_failed(db, settings, embedder) -> None:
    _add(db, settings, photo_bytes(1), "gone.jpg")
    _add(db, settings, photo_bytes(2), "ok.jpg")
    gone = _rows(db)["gone.jpg"]
    (settings.data_dir / gone.stored_path).unlink()
    worker = _make_worker(db, settings, embedder)
    _run(worker)
    rows = _rows(db)
    assert rows["gone.jpg"].analysis_status == "failed" and rows["gone.jpg"].analysis_error
    assert rows["ok.jpg"].analysis_status == "done"

    _add(db, settings, photo_bytes(3), "boom.jpg")
    embedder.fail = True
    worker = _make_worker(db, settings, embedder)
    _run(worker)
    boom = _rows(db)["boom.jpg"]
    assert boom.analysis_status == "failed" and "model exploded" in boom.analysis_error


def test_trashed_pending_files_skipped(db, settings: Settings, embedder) -> None:
    file_id = _add(db, settings, photo_bytes(1), "t.jpg")
    with db() as s:
        s.get(File, file_id).is_trashed = True
        s.commit()
    worker = _make_worker(db, settings, embedder)
    _run(worker)
    assert embedder.calls == []
    with db() as s:
        assert status_counts(s) == {"pending": 0, "done": 0, "failed": 0}


def test_model_unavailable_leaves_files_pending(db, settings: Settings, embedder) -> None:
    _add(db, settings, photo_bytes(1), "a.jpg")
    embedder.unavailable = True
    worker = _make_worker(db, settings, embedder)
    _run(worker)
    assert _rows(db)["a.jpg"].analysis_status == "pending"
    assert "weights not downloaded" in worker.error
    with db() as s:
        assert status_counts(s)["pending"] == 1


def test_gpu_embedder_gets_bigger_batches(db, settings: Settings, embedder) -> None:
    tuned = settings.model_copy(update={"embed_batch_size": 2, "embed_batch_size_gpu": 6})
    worker = _make_worker(db, tuned, embedder)
    assert worker.batch_limit() == 2
    embedder.device = "cuda"
    assert worker.batch_limit() == 6
    for i in range(6):
        _add(db, tuned, photo_bytes(i), f"{i}.jpg")
    _run(worker)
    assert max(embedder.calls) > 2 and sum(embedder.calls) == 6
    assert all(r.analysis_status == "done" for r in _rows(db).values())
