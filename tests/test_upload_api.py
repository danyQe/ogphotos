import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.models import File
from tests.fakes import BASE_URL, FakeEmbedder
from tests.imgutil import image_bytes, mp4_bytes, photo_bytes

JPG = image_bytes(exif_date="2021:05:06 07:08:09")
RESULT_KEYS = {
    "date_fixed",
    "filename",
    "status",
    "file_id",
    "existing_file_id",
    "existing_is_trashed",
    "similar_file_ids",
    "message",
}


def _post(client: TestClient, items: list[tuple[str, bytes]], mtimes: list[int] | None = None):
    files = [
        ("files", (p.rsplit("/", 1)[-1], data, "application/octet-stream")) for p, data in items
    ]
    data: dict[str, list[str]] = {"paths": [p for p, _ in items]}
    if mtimes is not None:
        data["mtimes"] = [str(m) for m in mtimes]
    return client.post("/api/upload", files=files, data=data)


def _count(client: TestClient) -> int:
    with client.app.state.session_factory() as s:
        return s.scalar(select(func.count(File.id)))


def test_same_file_twice_stores_one_copy(client: TestClient, settings: Settings) -> None:
    first = _post(client, [("a.jpg", JPG)]).json()["results"]
    second = _post(client, [("b.jpg", JPG)]).json()["results"]
    assert [r["status"] for r in first + second] == ["new", "exact_duplicate"]
    assert second[0]["existing_file_id"] == first[0]["file_id"]
    assert set(second[0]) == RESULT_KEYS
    assert _count(client) == 1
    assert len([p for p in settings.library_dir.rglob("*") if p.is_file()]) == 1


def test_duplicate_within_one_request(client: TestClient) -> None:
    r = _post(client, [("2021/a.jpg", JPG), ("Album/a.jpg", JPG)])
    assert [x["status"] for x in r.json()["results"]] == ["new", "exact_duplicate"]


def test_sidecar_in_same_request_sets_date(client: TestClient) -> None:
    sidecar = json.dumps({"photoTakenTime": {"timestamp": "1546300800"}}).encode()
    items = [
        ("T/2019/p.png", image_bytes()),
        ("T/2019/p.png.supplemental-metadata.json", sidecar),
    ]
    r = _post(client, items)
    results = r.json()["results"]
    assert [x["filename"] for x in results] == ["T/2019/p.png"]  # sidecars not reported
    with client.app.state.session_factory() as s:
        row = s.get(File, results[0]["file_id"])
        assert (row.taken_at_source, row.taken_at.year) == ("sidecar", 2019)


def test_mtimes_used_as_fallback(client: TestClient) -> None:
    r = _post(client, [("p.png", image_bytes())], mtimes=[1488585600000])  # 2017-03-04 UTC
    with client.app.state.session_factory() as s:
        row = s.get(File, r.json()["results"][0]["file_id"])
        assert (row.taken_at_source, row.taken_at.year) == ("mtime", 2017)


def test_mismatched_paths_rejected(client: TestClient) -> None:
    files = [("files", ("a.jpg", JPG, "image/jpeg")), ("files", ("b.jpg", JPG, "image/jpeg"))]
    r = client.post("/api/upload", files=files, data={"paths": ["a.jpg"]})
    assert r.status_code == 422
    assert _count(client) == 0


def test_without_paths_uses_upload_filename(client: TestClient) -> None:
    r = client.post("/api/upload", files=[("files", ("x.jpg", JPG, "image/jpeg"))])
    assert r.json()["results"][0]["filename"] == "x.jpg"


def test_errors_and_videos(client: TestClient, settings: Settings) -> None:
    r = _post(client, [("bad.jpg", b"nope"), ("v.mp4", mp4_bytes())]).json()["results"]
    assert [x["status"] for x in r] == ["error", "new"]
    assert list(settings.tmp_dir.iterdir()) == []


def test_thumb_route(client: TestClient) -> None:
    fid = _post(client, [("a.jpg", JPG)]).json()["results"][0]["file_id"]
    t = client.get(f"/api/files/{fid}/thumb")
    assert t.status_code == 200 and t.headers["content-type"] == "image/jpeg"
    assert client.get("/api/files/9999/thumb").status_code == 404


def test_resized_copy_reports_similar(client: TestClient) -> None:
    [first] = _post(client, [("a.jpg", photo_bytes(1, size=(1200, 900)))]).json()["results"]
    [copy] = _post(client, [("b.jpg", photo_bytes(1, size=(300, 225)))]).json()["results"]
    assert copy["status"] == "similar_exists"
    assert copy["similar_file_ids"] == [first["file_id"]]
    assert copy["file_id"] is not None
    assert _count(client) == 2


def test_phash_index_rebuilt_on_startup(settings: Settings) -> None:
    with TestClient(create_app(settings, FakeEmbedder()), base_url=BASE_URL) as c:
        [first] = _post(c, [("a.jpg", photo_bytes(1))]).json()["results"]
    with TestClient(create_app(settings, FakeEmbedder()), base_url=BASE_URL) as c:
        [copy] = _post(c, [("b.png", photo_bytes(1, fmt="PNG"))]).json()["results"]
    assert copy["status"] == "similar_exists"
    assert copy["similar_file_ids"] == [first["file_id"]]
