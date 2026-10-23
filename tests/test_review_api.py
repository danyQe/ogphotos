from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import File, GroupExclusion
from tests.imgutil import photo_bytes
from tests.test_groups_api import _burst, _upload


def _group(client: TestClient) -> dict[str, Any]:
    [g] = client.get("/api/groups").json()["groups"]
    return g


def _trashed(client: TestClient) -> set[int]:
    return {f["file_id"] for f in client.get("/api/trash").json()["files"]}


def _member_ids(client: TestClient, gid: int) -> set[int]:
    return {m["file_id"] for m in client.get(f"/api/groups/{gid}").json()["members"]}


def test_keep_best_then_restore_from_trash(client: TestClient) -> None:
    """M5 done-when: clean a group in one action and get it back from Trash."""
    burst, _ = _burst(client)
    g = _group(client)
    r = client.post(f"/api/groups/{g['id']}/keep-best")
    assert r.status_code == 200
    body = r.json()
    rest = set(burst) - {g["best_file_id"]}
    assert set(body["trashed"]) == rest and body["next_id"] is None
    assert _trashed(client) == rest
    assert client.get(f"/api/groups/{g['id']}").status_code == 404  # 1 photo left → no group
    assert client.get("/api/groups").json()["total"] == 0

    client.post("/api/trash/restore-all")
    again = _group(client)
    assert set(again["preview_file_ids"]) == set(burst)
    assert again["best_file_id"] == g["best_file_id"]


def test_keep_best_reports_next_group(client: TestClient) -> None:
    _burst(client)
    _upload(
        client,
        [(f"B_{i}.jpg", photo_bytes(4, size=(400 + 80 * i, 300 + 60 * i))) for i in range(2)],
    )
    groups = client.get("/api/groups").json()["groups"]
    assert [g["size"] for g in groups] == [3, 2]
    r = client.post(f"/api/groups/{groups[0]['id']}/keep-best").json()
    assert r["next_id"] == groups[1]["id"]


def test_apply_selection(client: TestClient) -> None:
    burst, others = _burst(client)
    g = _group(client)
    url = f"/api/groups/{g['id']}/apply"
    assert client.post(url, json={"keep": [], "trash": [others[0]]}).status_code == 400
    assert client.post(url, json={"keep": [burst[0]], "trash": [burst[0]]}).status_code == 400
    assert _trashed(client) == set()

    r = client.post(url, json={"keep": burst[:2], "trash": [burst[2]]}).json()
    assert r["trashed"] == [burst[2]]
    detail = client.get(f"/api/groups/{g['id']}").json()
    assert detail["reviewed"] is True and detail["size"] == 2
    assert _member_ids(client, g["id"]) == set(burst[:2])


def test_apply_with_nothing_trashed_marks_reviewed(client: TestClient) -> None:
    _burst(client)
    g = _group(client)
    r = client.post(f"/api/groups/{g['id']}/apply", json={"keep": [], "trash": []}).json()
    assert r["trashed"] == []
    assert client.get(f"/api/groups/{g['id']}").json()["reviewed"] is True
    assert client.post("/api/groups/999/apply", json={}).status_code == 404


def test_not_similar_splits_out_for_good(client: TestClient) -> None:
    burst, _ = _burst(client)
    g = _group(client)
    out = next(i for i in burst if i != g["best_file_id"])
    r = client.post(f"/api/groups/{g['id']}/remove/{out}").json()
    assert r["group_exists"] is True and r["new_group_id"] is None
    assert out not in _member_ids(client, g["id"])
    assert client.get(f"/api/groups/{g['id']}").json()["size"] == 2
    with client.app.state.session_factory() as s:
        pairs = {(e.file_id_a, e.file_id_b) for e in s.scalars(select(GroupExclusion))}
        assert pairs == {tuple(sorted((out, o))) for o in burst if o != out}
        assert s.get(File, out).group_id is None

    # trash + restore re-runs grouping; the exclusions keep it out
    client.post(f"/api/files/{out}/trash")
    client.post(f"/api/files/{out}/restore")
    assert out not in _member_ids(client, g["id"])

    assert client.post(f"/api/groups/{g['id']}/remove/{out}").status_code == 400
    last = client.post(f"/api/groups/{g['id']}/remove/{g['best_file_id']}").json()
    assert last["group_exists"] is False  # one photo left → group dissolved


def test_mark_reviewed(client: TestClient) -> None:
    _burst(client)
    g = _group(client)
    assert client.post(f"/api/groups/{g['id']}/reviewed").json()["reviewed"] is True
    assert _group(client)["reviewed"] is True
    client.post(f"/api/groups/{g['id']}/reviewed", params={"reviewed": "false"})
    assert _group(client)["reviewed"] is False
    assert client.post("/api/groups/999/reviewed").status_code == 404
    assert client.post("/api/groups/999/keep-best").status_code == 404
    assert client.post("/api/groups/999/remove/1").status_code == 404


def test_review_page_has_actions(client: TestClient) -> None:
    _burst(client)
    g = _group(client)
    page = client.get(f"/groups/{g['id']}").text
    assert f'data-group-id="{g["id"]}"' in page and "/static/review.js" in page
    assert "Keep best, trash rest" in page and page.count("Not similar") == 3
    assert '<span class="num">3</span>' in page
