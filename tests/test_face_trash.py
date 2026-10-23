from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Face, FaceExclusion, File, Person
from tests.faceutil import face_vec
from tests.fakes import FakeFace
from tests.imgutil import photo_bytes
from tests.test_groups_api import _upload


def _alice(client: TestClient, n: int = 4) -> list[int]:
    engine = client.app.state.face_engine
    sizes = [(400 + i, 300) for i in range(n)]
    for i, size in enumerate(sizes):
        engine.script[size] = [FakeFace((50, 50, 100, 100), face_vec(1, i), score=0.7 + i / 100)]
    ids = _upload(
        client, [(f"a{i}.jpg", photo_bytes(20 + i, size=sz)) for i, sz in enumerate(sizes)]
    )
    client.app.state.face_worker.run_pending()
    return ids


def _person(client: TestClient) -> Person:
    with client.app.state.session_factory() as s:
        return s.scalars(select(Person)).one()


def test_trash_and_restore_update_counts_and_index(client: TestClient) -> None:
    ids = _alice(client)
    assert _person(client).face_count == 4
    index = client.app.state.face_index
    client.post(f"/api/files/{ids[0]}/trash")
    assert _person(client).face_count == 3 and len(index) == 3
    client.post(f"/api/files/{ids[0]}/restore")
    assert _person(client).face_count == 4 and len(index) == 4


def test_trashing_cover_face_falls_back(client: TestClient) -> None:
    from app.services.people import cover_face_id

    _alice(client)
    with client.app.state.session_factory() as s:
        p = s.scalars(select(Person)).one()
        best = cover_face_id(s, p)
        best_file = s.get(Face, best).file_id
    client.post(f"/api/files/{best_file}/trash")
    with client.app.state.session_factory() as s:
        p = s.scalars(select(Person)).one()
        fallback = cover_face_id(s, p)
        assert fallback is not None and fallback != best
    assert client.get(f"/people/{p.id}").status_code in (200, 404)  # page exists after Task 11


def test_empty_trash_deletes_face_rows_crops_and_exclusions(client: TestClient) -> None:
    ids = _alice(client)
    state = client.app.state
    with state.session_factory() as s:
        face = s.scalars(select(Face).where(Face.file_id == ids[0])).one()
        pid = face.person_id
        s.get(Person, pid).cover_face_id = face.id
        s.add(FaceExclusion(face_id=face.id, person_id=pid))
        s.commit()
        face_id = face.id
    crop = state.settings.faces_dir / f"{face_id}.jpg"
    assert crop.exists()
    client.post(f"/api/files/{ids[0]}/trash")
    assert client.post("/api/trash/empty?confirm=true").status_code == 200
    with state.session_factory() as s:
        assert s.get(Face, face_id) is None
        assert s.get(FaceExclusion, (face_id, pid)) is None
        assert s.get(Person, pid).cover_face_id is None
        assert s.get(File, ids[0]) is None
    assert not crop.exists()


def test_restore_waits_for_clusterer_lock_before_writing(client: TestClient) -> None:
    import threading
    import time

    ids = _alice(client)
    client.post(f"/api/files/{ids[0]}/trash")
    lock = client.app.state.clusterer.lock
    held = threading.Event()

    def hold() -> None:
        with lock:
            held.set()
            time.sleep(0.3)

    t = threading.Thread(target=hold)
    t.start()
    held.wait()
    assert client.post(f"/api/files/{ids[0]}/restore").status_code == 200
    t.join()
    assert _person(client).face_count == 4
