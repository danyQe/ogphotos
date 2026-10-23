from fastapi.testclient import TestClient

from tests.imgutil import mp4_bytes, photo_bytes


def _upload(client: TestClient, name: str, data: bytes) -> int:
    files = [("files", (name, data, "application/octet-stream"))]
    return client.post("/api/upload", files=files).json()["results"][0]["file_id"]


def _wait(client: TestClient) -> None:
    assert client.app.state.worker.wait_idle(timeout=30)


def test_uploads_get_analyzed_and_status_reports(client: TestClient) -> None:
    a = _upload(client, "a.jpg", photo_bytes(1, size=(1600, 1200)))
    b = _upload(client, "b.jpg", photo_bytes(1, size=(400, 300)))
    c = _upload(client, "c.jpg", photo_bytes(2))
    _upload(client, "v.mp4", mp4_bytes())
    _wait(client)

    status = client.get("/api/status").json()
    assert {k: status[k] for k in ("pending", "done", "failed")} == {
        "pending": 0,
        "done": 3,
        "failed": 0,
    }
    assert status["model"]["model"] == "fake"

    r = client.get(f"/api/files/{a}/similar").json()
    assert [n["file_id"] for n in r["neighbors"]] == [b]
    assert r["neighbors"][0]["cosine"] > 0.92
    assert r["neighbors"][0]["phash_distance"] <= 8
    loose = client.get(f"/api/files/{a}/similar", params={"min_cosine": -1}).json()
    assert [n["file_id"] for n in loose["neighbors"]] == [b, c]


def test_similar_errors(client: TestClient) -> None:
    v = _upload(client, "v.mp4", mp4_bytes())
    assert client.get(f"/api/files/{v}/similar").status_code == 409
    assert client.get("/api/files/999/similar").status_code == 404


def test_analysis_resumes_with_index_after_restart(settings) -> None:
    from app.main import create_app
    from tests.fakes import BASE_URL, FakeEmbedder

    with TestClient(create_app(settings, FakeEmbedder()), base_url=BASE_URL) as c:
        a = _upload(c, "a.jpg", photo_bytes(1))
        _wait(c)
    with TestClient(create_app(settings, FakeEmbedder()), base_url=BASE_URL) as c:
        b = _upload(c, "b.png", photo_bytes(1, size=(500, 375), fmt="PNG"))
        _wait(c)
        r = c.get(f"/api/files/{b}/similar").json()
    assert [n["file_id"] for n in r["neighbors"]] == [a]
