from fastapi.testclient import TestClient

from tests.faceutil import face_vec
from tests.fakes import FakeFace
from tests.imgutil import photo_bytes
from tests.test_groups_api import _upload


def _two_people(client: TestClient) -> tuple[int, int]:
    engine = client.app.state.face_engine
    items = []
    for p in (1, 2):
        for i in range(3):
            size = (500 + p * 10 + i, 300)
            engine.script[size] = [FakeFace((50, 50, 100, 100), face_vec(p, i))]
            items.append((f"p{p}_{i}.jpg", photo_bytes(p * 10 + i, size=size)))
    _upload(client, items)
    client.app.state.face_worker.run_pending()
    people = client.get("/api/people").json()["people"]
    assert [p["label"] for p in people] == ["Person 1", "Person 2"]
    return people[0]["person_id"], people[1]["person_id"]


def test_people_endpoints(client: TestClient) -> None:
    a, b = _two_people(client)
    body = client.get(f"/api/people/{a}").json()
    assert body["total"] == 3 and len(body["photos"]) == 3
    faces = client.get(f"/api/people/{a}/faces").json()["faces"]
    assert len(faces) == 3
    crop = client.get(f"/api/faces/{faces[0]['face_id']}/crop")
    assert crop.status_code == 200 and crop.headers["content-type"] == "image/jpeg"

    r = client.patch(f"/api/people/{a}", json={"name": "Alice"})
    assert r.json()["label"] == "Alice" and r.json()["same_name"] == []
    assert client.patch(f"/api/people/{b}", json={"name": "Alice"}).json()["same_name"] == [a]
    assert (
        client.patch(f"/api/people/{b}", json={"cover_face_id": faces[0]["face_id"]}).status_code
        == 400
    )
    client.patch(f"/api/people/{b}", json={"hidden": True})
    assert len(client.get("/api/people").json()["people"]) == 1
    assert len(client.get("/api/people?hidden=true").json()["people"]) == 2

    merged = client.post(f"/api/people/{b}/merge", json={"into_person_id": a}).json()
    assert merged["person_id"] == a and merged["face_count"] == 6
    assert client.get(f"/api/people/{b}").status_code == 404


def test_face_endpoints(client: TestClient) -> None:
    a, b = _two_people(client)
    fid = client.get(f"/api/people/{a}/faces").json()["faces"][0]["face_id"]
    r = client.post(f"/api/faces/{fid}/remove").json()
    assert r["person_id"] != a
    assert client.post(f"/api/faces/{fid}/remove").status_code in (200, 400)
    new = client.post("/api/faces/assign", json={"face_ids": [fid], "new": True}).json()
    assert new["person_id"] not in (a, b)
    assert client.post("/api/faces/assign", json={"face_ids": [fid], "person_id": a}).json() == {
        "person_id": a
    }
    assert client.post("/api/faces/assign", json={"face_ids": []}).status_code == 400

    file_id = client.get(f"/api/people/{a}").json()["photos"][0]["file_id"]
    chips = client.get(f"/api/files/{file_id}/faces").json()["faces"]
    assert chips[0]["label"] in ("Person 1", "Alice")
    assert client.get("/api/faces/unsorted").json()["total"] == 0
    assert client.get("/api/faces/999/crop").status_code == 404


def test_jobs_and_switches(client: TestClient) -> None:
    _two_people(client)
    assert client.post("/api/faces/recluster").status_code == 200
    client.app.state.recluster_job.wait(10)
    assert client.app.state.recluster_job.state.people == 2
    assert client.post("/api/faces/retry-failed").json() == {"queued": 0}
    assert client.put("/api/faces/scanning", json={"enabled": False}).json() == {"enabled": False}
    assert client.get("/api/status").json()["faces"]["enabled"] is False


def test_cross_site_writes_refused(client: TestClient) -> None:
    a, _ = _two_people(client)
    r = client.patch(
        f"/api/people/{a}", json={"name": "X"}, headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 403
