from fastapi.testclient import TestClient

from app.models import Group
from tests.imgutil import photo_bytes


def _upload(client: TestClient, items: list[tuple[str, bytes]]) -> list[int]:
    files = [("files", (n, d, "application/octet-stream")) for n, d in items]
    results = client.post("/api/upload", files=files).json()["results"]
    assert client.app.state.worker.wait_idle(timeout=30)
    return [r["file_id"] for r in results]


def _burst(client: TestClient) -> tuple[list[int], list[int]]:
    """Three shots of one scene plus two unrelated photos."""
    ids = _upload(
        client,
        [
            ("IMG_1.jpg", photo_bytes(1, size=(800, 600), exif_date="2022:06:01 12:00:01")),
            ("IMG_2.jpg", photo_bytes(1, size=(640, 480), exif_date="2022:06:01 12:00:02")),
            ("IMG_3.jpg", photo_bytes(1, size=(400, 300), exif_date="2022:06:01 12:00:03")),
            ("other.jpg", photo_bytes(2, exif_date="2021:01:01 00:00:00")),
            ("third.jpg", photo_bytes(3, exif_date="2021:01:01 00:00:00")),
        ],
    )
    return ids[:3], ids[3:]


def test_burst_uploaded_together_becomes_one_group(client: TestClient) -> None:
    burst, _ = _burst(client)
    body = client.get("/api/groups").json()
    assert body["total"] == 1
    [g] = body["groups"]
    assert g["size"] == 3 and g["reviewed"] is False
    assert g["best_file_id"] in burst
    assert g["preview_file_ids"][0] == g["best_file_id"]
    assert set(g["preview_file_ids"]) == set(burst)
    assert g["date_from"].startswith("2022-06-01T12:00:01")
    assert g["date_to"].startswith("2022-06-01T12:00:03")

    detail = client.get(f"/api/groups/{g['id']}").json()
    members = detail["members"]
    assert [m["file_id"] for m in members][0] == g["best_file_id"]  # best first
    assert [m["is_best"] for m in members] == [True, False, False]
    assert {m["file_id"] for m in members} == set(burst)
    assert members[0]["score"] >= members[1]["score"] >= members[2]["score"]
    assert {"width", "height", "size_bytes", "taken_at", "sharpness", "original_name"} <= set(
        members[0]
    )


def test_group_list_filters_and_paging(client: TestClient) -> None:
    _burst(client)
    gid = client.get("/api/groups").json()["groups"][0]["id"]
    assert client.get("/api/groups", params={"reviewed": "true"}).json()["total"] == 0
    with client.app.state.session_factory() as s:
        s.get(Group, gid).reviewed = True
        s.commit()
    assert client.get("/api/groups", params={"reviewed": "true"}).json()["total"] == 1
    assert client.get("/api/groups", params={"reviewed": "false"}).json()["total"] == 0
    assert client.get("/api/groups", params={"year": 2022}).json()["total"] == 1
    assert client.get("/api/groups", params={"year": 2019}).json()["total"] == 0
    empty_page = client.get("/api/groups", params={"page": 2}).json()
    assert empty_page["total"] == 1 and empty_page["groups"] == []


def test_unknown_group_404(client: TestClient) -> None:
    assert client.get("/api/groups/999").status_code == 404
    assert client.get("/groups/999").status_code == 404


def test_groups_pages_render(client: TestClient) -> None:
    assert "No similar-photo groups yet" in client.get("/groups").text
    burst, _ = _burst(client)
    page = client.get("/groups").text
    gid = client.get("/api/groups").json()["groups"][0]["id"]
    assert f'href="/groups/{gid}"' in page
    assert "3 photos" in page
    detail = client.get(f"/groups/{gid}").text
    assert "Suggested best" in detail
    for fid in burst:
        assert f"/api/files/{fid}/thumb" in detail
    assert "800 × 600" in detail


def test_original_route(client: TestClient) -> None:
    data = photo_bytes(9)
    [fid] = _upload(client, [("x.jpg", data)])
    r = client.get(f"/api/files/{fid}/original")
    assert r.status_code == 200 and r.content == data
    assert r.headers["content-type"] == "image/jpeg"
    assert client.get("/api/files/999/original").status_code == 404
