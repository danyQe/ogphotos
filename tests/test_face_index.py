from app.services.face_index import FaceIndex
from tests.faceutil import add_face, face_vec, make_file


def test_load_skips_trashed_files(db) -> None:
    with db() as s:
        tmp = FaceIndex(512)
        live = add_face(s, tmp, face_vec(1, 1))
        add_face(s, tmp, face_vec(1, 2), file=make_file(s, trashed=True))
        s.commit()
        index = FaceIndex.load(s)
    assert index.ids() == [live.id]
    assert index.search(face_vec(1, 3), 0.5, 10)[0][0] == live.id
