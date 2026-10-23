import json
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.models import File
from app.services import importer as importer_module
from tests.imgutil import photo_bytes

TAKEN_2019 = 1561982400  # 2019-07-01 12:00:00 UTC


def _tree(root: Path) -> dict[str, tuple[bytes, float]]:
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def takeout(tmp_path: Path) -> Path:
    """A small Takeout-like export: a year folder, an album copy, a sidecar, junk."""
    root = tmp_path / "Takeout" / "Google Photos"
    year = root / "Photos from 2019"
    album = root / "Trip"
    for d in (year, album):
        d.mkdir(parents=True)
    burst = [photo_bytes(1, size=(800, 600)), photo_bytes(1, size=(640, 480))]
    (year / "IMG_1.jpg").write_bytes(burst[0])
    (year / "IMG_1.jpg.supplemental-metadata.json").write_text(
        json.dumps({"photoTakenTime": {"timestamp": str(TAKEN_2019)}})
    )
    (year / "IMG_2.jpg").write_bytes(burst[1])
    (year / "IMG_3.jpg").write_bytes(photo_bytes(2))
    (album / "IMG_1.jpg").write_bytes(burst[0])  # Takeout's album duplicate
    (album / "metadata.json").write_text("{}")
    (album / "notes.txt").write_text("not a photo")
    (album / ".DS_Store").write_bytes(b"\0")
    return root


def _run(client: TestClient, path: Path | str) -> dict[str, Any]:
    r = client.post("/api/import-folder", json={"path": str(path)})
    assert r.status_code == 200, r.text
    job = r.json()
    importer = client.app.state.importer
    assert importer.wait(job["id"], timeout=60)
    assert client.app.state.worker.wait_idle(timeout=60)
    return client.get(f"/api/import-folder/{job['id']}").json()


def test_import_folder_end_to_end(client: TestClient, takeout: Path) -> None:
    before = _tree(takeout)
    job = _run(client, takeout)

    assert job["status"] == "done" and job["total"] == 5 and job["processed"] == 5
    by_name = {r["filename"]: r for r in job["results"]}
    assert by_name["Trip/IMG_1.jpg"]["status"] == "exact_duplicate"
    assert by_name["Trip/notes.txt"]["status"] == "error"
    assert ".DS_Store" not in " ".join(by_name) and "metadata.json" not in " ".join(by_name)
    assert job["counts"]["exact_duplicate"] == 1 and job["counts"]["error"] == 1
    assert job["counts"].get("new", 0) + job["counts"].get("similar_exists", 0) == 3

    assert _tree(takeout) == before  # source untouched: same files, bytes and mtimes

    with client.app.state.session_factory() as s:
        img1 = s.get(File, by_name["Photos from 2019/IMG_1.jpg"]["file_id"])
        assert img1.taken_at_source == "sidecar" and img1.taken_at.year == 2019
        assert img1.stored_path.startswith("library/2019/")
        assert img1.analysis_status == "done"
    # the burst (IMG_1 + IMG_2) is grouped once analysis finishes
    [g] = client.get("/api/groups").json()["groups"]
    assert g["size"] == 2

    # polling with an offset returns only the newer rows
    tail = client.get(f"/api/import-folder/{job['id']}", params={"offset": 4}).json()
    assert len(tail["results"]) == 1 and tail["offset"] == 4

    again = _run(client, takeout)  # importing twice stores nothing new
    assert again["counts"].get("new", 0) == 0 and again["counts"].get("similar_exists", 0) == 0
    assert "date_fixed" not in again["counts"]  # dates were right the first time


@pytest.mark.parametrize("bad", ["", "relative/path", "Z:/definitely/not/here/123"])
def test_import_rejects_bad_paths(client: TestClient, bad: str) -> None:
    r = client.post("/api/import-folder", json={"path": bad})
    assert r.status_code == 400


def test_import_refuses_the_data_folder(client: TestClient) -> None:
    data = client.app.state.settings.data_dir
    for path in (data, data / "library", data.parent):
        r = client.post("/api/import-folder", json={"path": str(path.resolve())})
        assert r.status_code == 400 and "data folder" in r.json()["detail"]


def test_import_cancel_and_single_job(
    client: TestClient, takeout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate, entered = threading.Event(), threading.Event()
    real = importer_module.ingest_file

    def slow(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        gate.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(importer_module, "ingest_file", slow)
    job = client.post("/api/import-folder", json={"path": str(takeout)}).json()
    assert entered.wait(10)
    busy = client.post("/api/import-folder", json={"path": str(takeout)})
    assert busy.status_code == 400 and "already running" in busy.json()["detail"]
    assert client.get("/import").text.count(job["id"]) == 1  # page resumes the running job

    assert client.post(f"/api/import-folder/{job['id']}/cancel").status_code == 200
    gate.set()
    assert client.app.state.importer.wait(job["id"], timeout=30)
    done = client.get(f"/api/import-folder/{job['id']}").json()
    assert done["status"] == "cancelled" and done["processed"] == 1
    assert client.get("/api/import-folder/nope").status_code == 404
    assert client.post("/api/import-folder/nope/cancel").status_code == 404


def test_import_page_renders(client: TestClient) -> None:
    page = client.get("/import").text
    assert "Import a folder" in page and "/static/import.js" in page
    assert "never changed" in page


def test_import_fixes_dates_of_photos_uploaded_without_sidecars(
    client: TestClient, takeout: Path
) -> None:
    data = (takeout / "Photos from 2019" / "IMG_1.jpg").read_bytes()
    [first] = client.post("/api/upload", files=[("files", ("IMG_1.jpg", data))]).json()["results"]
    with client.app.state.session_factory() as s:
        assert s.get(File, first["file_id"]).taken_at_source == "upload"

    job = _run(client, takeout)
    assert job["counts"]["date_fixed"] == 1
    with client.app.state.session_factory() as s:
        row = s.get(File, first["file_id"])
        assert row.taken_at_source == "sidecar" and row.taken_at.year == 2019
