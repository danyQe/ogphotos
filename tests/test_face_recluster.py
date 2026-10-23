from sqlalchemy import select

from app.config import Settings
from app.models import Face, FaceExclusion, Person
from app.services.face_cluster import PeopleClusterer
from app.services.face_index import FaceIndex
from tests.faceutil import add_face, face_vec, make_file


def _build(db, settings: Settings):
    c = PeopleClusterer(settings, FaceIndex(512))
    with db() as s:
        faces = {p: [add_face(s, c.index, face_vec(p, i)) for i in range(4)] for p in (1, 2, 3)}
        for group in faces.values():
            for f in group:
                c.assign(s, f.id)
        s.commit()
        ids = {p: [f.id for f in g] for p, g in faces.items()}
    return c, ids


def _owner(db, face_id: int) -> int | None:
    with db() as s:
        return s.get(Face, face_id).person_id


def test_recluster_keeps_unnamed_labels(db, settings: Settings) -> None:
    c, ids = _build(db, settings)
    before = {p: _owner(db, ids[p][0]) for p in ids}
    assert c.recluster(db) == 3
    assert {p: _owner(db, ids[p][0]) for p in ids} == before
    with db() as s:
        assert sorted(s.scalars(select(Person.label_no))) == [1, 2, 3]


def test_recluster_keeps_names_user_choices_and_exclusions(db, settings: Settings) -> None:
    c, ids = _build(db, settings)
    with db() as s:
        alice = s.get(Person, _owner(db, ids[1][0]))
        alice.name = "Alice"
        # user put one of person 2's faces into Alice by hand
        moved = s.get(Face, ids[2][0])
        moved.person_id, moved.assigned_by = alice.id, "user"
        # and said face ids[3][0] is not person 3
        p3 = _owner(db, ids[3][0])
        s.add(FaceExclusion(face_id=ids[3][0], person_id=p3))
        s.get(Face, ids[3][0]).person_id = None
        s.commit()
    c.recluster(db)
    assert _owner(db, ids[2][0]) == alice.id
    assert all(_owner(db, f) == alice.id for f in ids[1])
    assert _owner(db, ids[3][0]) != p3


def test_recluster_applies_new_threshold(db, settings: Settings) -> None:
    c, ids = _build(db, settings)
    with db() as s:
        from app.services.tunables import update_tunables

        update_tunables(s, {"face_match_cosine": 0.9})  # nothing is that similar
    assert c.recluster(db) == 0
    with db() as s:
        assert s.scalars(select(Person)).all() == []


def test_recluster_keeps_trashed_faces_and_trash_only_people(db, settings: Settings) -> None:
    c, ids = _build(db, settings)
    with db() as s:
        p1 = _owner(db, ids[1][0])
        trashed_in_p1 = add_face(s, c.index, face_vec(1, 9), make_file(s, trashed=True))
        trashed_in_p1.person_id, trashed_in_p1.assigned_by = p1, "auto"
        only_trash = c.new_person(s)
        t_only = add_face(s, c.index, face_vec(7, 0), make_file(s, trashed=True))
        t_only.person_id, t_only.assigned_by = only_trash.id, "auto"
        s.add(FaceExclusion(face_id=ids[2][0], person_id=only_trash.id))
        s.commit()
        a, b, only_id = trashed_in_p1.id, t_only.id, only_trash.id
    c.recluster(db)
    assert _owner(db, a) == p1
    assert _owner(db, b) == only_id
    with db() as s:
        assert s.get(Person, only_id) is not None
        assert s.scalars(select(FaceExclusion).where(FaceExclusion.person_id == only_id)).all()
