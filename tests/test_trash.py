from pathlib import Path

from fastapi.testclient import TestClient

from app.models import File, Group
from tests.imgutil import photo_bytes
from tests.test_groups_api import _burst, _upload


def _row(client: TestClient, file_id: int) -> File | None:
    with client.app.state.session_factory() as s:
        return s.get(File, file_id)


def _path(client: TestClient, file_id: int) -> Path:
    return client.app.state.settings.data_dir / _row(client, file_id).stored_path


def _group_of(client: TestClient, file_id: int) -> Group | None:
    with client.app.state.session_factory() as s:
        gid = s.get(File, file_id).group_id
        return s.get(Group, gid) if gid is not None else None


def test_trash_moves_file_and_drops_it_from_indexes(client: TestClient) -> None:
    data = photo_bytes(5)
    [fid] = _upload(client, [("a.jpg", data)])
    before = _path(client, fid)
    state = client.app.state
    assert state.vector_index.get(fid) is not None and len(state.phash_index) == 1

    r = client.post(f"/api/files/{fid}/trash")
    assert r.status_code == 200 and r.json()["is_trashed"] is True
    row = _row(client, fid)
    assert row.is_trashed and row.trashed_at is not None
    after = _path(client, fid)
    assert row.stored_path.startswith("trash/")
    assert after.relative_to(state.settings.trash_dir) == before.relative_to(
        state.settings.library_dir
    )
    assert not before.exists() and after.read_bytes() == data  # moved, untouched
    assert state.vector_index.get(fid) is None and len(state.phash_index) == 0
    body = client.get("/api/trash").json()
    assert body["total"] == 1 and body["files"][0]["file_id"] == fid
    assert client.post("/api/files/999/trash").status_code == 404


def test_trashing_members_shrinks_then_dissolves_group(client: TestClient) -> None:
    burst, _ = _burst(client)
    group = _group_of(client, burst[0])
    best = group.best_file_id
    client.post(f"/api/files/{best}/trash")
    left = [i for i in burst if i != best]
    shrunk = _group_of(client, left[0])
    assert shrunk.id == group.id and shrunk.size == 2 and shrunk.best_file_id in left
    assert _row(client, best).group_id is None

    client.post(f"/api/files/{left[0]}/trash")
    assert _group_of(client, left[1]) is None
    assert client.get(f"/api/groups/{group.id}").status_code == 404


def test_restore_moves_back_reindexes_and_regroups(client: TestClient) -> None:
    burst, _ = _burst(client)
    originals = {i: _path(client, i) for i in burst}
    for i in burst:
        client.post(f"/api/files/{i}/trash")
    assert client.get("/api/groups").json()["total"] == 0

    assert client.post("/api/trash/restore-all").json() == {"restored": 3}
    for i in burst:
        assert _path(client, i) == originals[i] and originals[i].exists()
        assert not _row(client, i).is_trashed
        assert client.app.state.vector_index.get(i) is not None
    [g] = client.get("/api/groups").json()["groups"]
    assert set(g["preview_file_ids"]) == set(burst)
    assert client.get("/api/trash").json()["total"] == 0


def test_restore_one_file(client: TestClient) -> None:
    [fid] = _upload(client, [("a.jpg", photo_bytes(5))])
    client.post(f"/api/files/{fid}/trash")
    r = client.post(f"/api/files/{fid}/restore")
    assert r.status_code == 200 and r.json()["is_trashed"] is False
    assert _path(client, fid).exists() and len(client.app.state.phash_index) == 1


def test_restore_never_overwrites_a_file_in_the_way(client: TestClient) -> None:
    [fid] = _upload(client, [("a.jpg", photo_bytes(5))])
    library_path = _path(client, fid)
    client.post(f"/api/files/{fid}/trash")
    library_path.write_bytes(b"someone else")
    client.post(f"/api/files/{fid}/restore")
    assert library_path.read_bytes() == b"someone else"
    restored = _path(client, fid)
    assert restored != library_path and restored.stem.endswith("-1") and restored.exists()


def test_restore_requeues_files_that_were_not_analyzed(client: TestClient) -> None:
    [fid] = _upload(client, [("a.jpg", photo_bytes(5))])
    client.post(f"/api/files/{fid}/trash")
    with client.app.state.session_factory() as s:
        row = s.get(File, fid)
        row.analysis_status, row.analysis_error = "failed", "could not read image: gone"
        s.commit()
    client.post(f"/api/files/{fid}/restore")
    assert client.app.state.worker.wait_idle(timeout=30)
    assert _row(client, fid).analysis_status == "done"


def test_empty_trash_requires_confirm_and_deletes_only_trash(client: TestClient) -> None:
    data = photo_bytes(5)
    keep, gone = _upload(client, [("keep.jpg", photo_bytes(6)), ("gone.jpg", data)])
    client.post(f"/api/files/{gone}/trash")
    gone_path, keep_path = _path(client, gone), _path(client, keep)
    thumb = client.app.state.settings.thumbs_dir / f"{gone}.jpg"
    assert thumb.exists()

    r = client.post("/api/trash/empty")
    assert r.status_code == 400 and "1 file" in r.json()["detail"]
    assert gone_path.exists()

    assert client.post("/api/trash/empty", params={"confirm": "true"}).json() == {"deleted": 1}
    assert not gone_path.exists() and not thumb.exists() and _row(client, gone) is None
    assert keep_path.exists() and _row(client, keep) is not None
    # gone for good: the same bytes now upload as new
    [again] = client.post("/api/upload", files=[("files", ("gone.jpg", data))]).json()["results"]
    assert again["status"] == "new"


def test_empty_trash_never_deletes_outside_trash_dir(client: TestClient) -> None:
    [fid] = _upload(client, [("a.jpg", photo_bytes(5))])
    library_path = _path(client, fid)
    client.post(f"/api/files/{fid}/trash")
    trashed_rel = _row(client, fid).stored_path
    with client.app.state.session_factory() as s:
        s.get(File, fid).stored_path = "library/" + trashed_rel.removeprefix("trash/")
        s.commit()
    library_path.write_bytes(b"precious")
    client.post("/api/trash/empty", params={"confirm": "true"})
    assert library_path.read_bytes() == b"precious"


def test_trash_page(client: TestClient) -> None:
    assert "Trash is empty" in client.get("/trash").text
    [fid] = _upload(client, [("holiday.jpg", photo_bytes(5))])
    client.post(f"/api/files/{fid}/trash")
    page = client.get("/trash").text
    assert "holiday.jpg" in page and f'data-file-id="{fid}"' in page
    assert "Empty trash" in page and "/static/trash.js" in page
