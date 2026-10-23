from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Face, File, Person
from tests.faceutil import face_vec
from tests.fakes import FakeFace
from tests.imgutil import image_bytes, photo_bytes
from tests.test_groups_api import _upload


def _script(client: TestClient, size: tuple[int, int], *faces: FakeFace) -> None:
    client.app.state.face_engine.script[size] = list(faces)


def _rows(client: TestClient, model, *where):
    with client.app.state.session_factory() as s:
        return s.scalars(select(model).where(*where)).all()


def test_faces_found_cropped_and_clustered(client: TestClient) -> None:
    sizes = [(320 + i, 240) for i in range(3)]
    for i, size in enumerate(sizes):
        _script(client, size, FakeFace((40, 30, 100, 120), face_vec(1, i)))
    _upload(client, [(f"a{i}.jpg", photo_bytes(i, size=sz)) for i, sz in enumerate(sizes)])
    client.app.state.face_worker.run_pending()  # the background thread may have done some

    faces = _rows(client, Face)
    faces.sort(key=lambda f: f.id)
    assert len(faces) == 3 and len({f.person_id for f in faces}) == 1
    f0 = faces[0]
    assert abs(f0.box_x - 40 / 320) < 1e-6 and abs(f0.box_h - 120 / 240) < 1e-6
    assert (client.app.state.settings.faces_dir / f"{f0.id}.jpg").is_file()
    files = _rows(client, File)
    assert all(f.face_status == "done" and f.face_count == 1 for f in files)
    [person] = _rows(client, Person)
    assert person.face_count == 3
    status = client.get("/api/status").json()["faces"]
    assert status["done"] == 3 and status["pending"] == 0 and status["model_installed"] is True


def test_small_and_unsure_faces_skipped(client: TestClient) -> None:
    _script(
        client,
        (330, 240),
        FakeFace((0, 0, 40, 40), face_vec(1, 1)),
        FakeFace((100, 100, 80, 80), face_vec(2, 1), score=0.3),
    )
    _upload(client, [("a.jpg", photo_bytes(1, size=(330, 240)))])
    client.app.state.face_worker.run_pending()
    assert _rows(client, Face) == []
    assert _rows(client, File)[0].face_status == "done"


def test_rotated_photo_boxes_are_upright(client: TestClient) -> None:
    # Stored 320×240 with Orientation 6 → upright 240×320; detector must see the upright one
    _script(client, (240, 320), FakeFace((24, 32, 120, 160), face_vec(1, 1)))
    _upload(client, [("r.jpg", image_bytes(size=(320, 240), orientation=6))])
    client.app.state.face_worker.run_pending()
    [face] = _rows(client, Face)
    assert (face.box_x, face.box_y, face.box_w, face.box_h) == (0.1, 0.1, 0.5, 0.5)


def test_unreadable_file_fails_alone(client: TestClient) -> None:
    from app.services.face_worker import set_scanning

    with client.app.state.session_factory() as s:
        set_scanning(s, False)  # keep the background thread off these files
    _script(client, (341, 240), FakeFace((40, 30, 100, 120), face_vec(1, 1)))
    ids = _upload(
        client,
        [("a.jpg", photo_bytes(1, size=(341, 240))), ("b.jpg", photo_bytes(2, size=(342, 240)))],
    )
    settings = client.app.state.settings
    with client.app.state.session_factory() as s:
        (settings.data_dir / s.get(File, ids[1]).stored_path).unlink()
        set_scanning(s, True)
    client.app.state.face_worker.run_pending()
    a, b = (_rows(client, File, File.id == i)[0] for i in ids)
    assert a.face_status == "done" and a.face_count == 1
    assert b.face_status == "failed" and b.face_error


def test_missing_model_leaves_files_pending(client: TestClient) -> None:
    engine = client.app.state.face_engine
    engine.installed = False
    _upload(client, [("a.jpg", photo_bytes(1))])
    assert client.app.state.face_worker.run_pending() == 0
    assert _rows(client, File)[0].face_status == "pending"
    status = client.get("/api/status").json()["faces"]
    assert status["model_installed"] is False and "not installed" in status["error"]
    assert client.get("/library").status_code == 200  # the rest of the app works


def test_videos_and_disabled_scanning_skipped(client: TestClient) -> None:
    from app.services.face_worker import set_scanning
    from tests.imgutil import mp4_bytes

    _upload(client, [("v.mp4", mp4_bytes())])
    assert _rows(client, File)[0].face_status == "skipped"
    with client.app.state.session_factory() as s:
        set_scanning(s, False)
    _upload(client, [("a.jpg", photo_bytes(3))])
    assert client.app.state.face_worker.run_pending() == 0
    assert client.get("/api/status").json()["faces"]["enabled"] is False
