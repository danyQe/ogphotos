import pytest

from app.config import Settings
from app.models import Face, FaceExclusion, Person
from app.services.face_cluster import PeopleClusterer
from app.services.face_index import FaceIndex
from app.services.people import (
    People,
    cover_face_id,
    file_faces,
    list_people,
    person_photos,
    unsorted_faces,
)
from tests.faceutil import add_face, face_vec


@pytest.fixture
def world(db, settings: Settings):
    c = PeopleClusterer(settings, FaceIndex(512))
    with db() as s:
        groups = {}
        for p in (1, 2):
            fs = [add_face(s, c.index, face_vec(p, i), score=0.5 + i / 10) for i in range(3)]
            for f in fs:
                c.assign(s, f.id)
            groups[p] = [f.id for f in fs]
        s.commit()
    return People(c), c, groups


def _pid(db, face_id):
    with db() as s:
        return s.get(Face, face_id).person_id


def test_rename_trims_limits_and_reports_same_name(db, world) -> None:
    people, _, g = world
    a, b = _pid(db, g[1][0]), _pid(db, g[2][0])
    with db() as s:
        assert people.rename(s, a, "  Alice  ") == []
        assert s.get(Person, a).name == "Alice"
        assert people.rename(s, b, "Alice") == [a]
        people.rename(s, b, "")
        assert s.get(Person, b).name is None
        people.rename(s, b, "x" * 500)
        assert len(s.get(Person, b).name) == 100
        with pytest.raises(LookupError):
            people.rename(s, 999, "Z")


def test_merge_moves_faces_and_name(db, world) -> None:
    people, _, g = world
    a, b = _pid(db, g[1][0]), _pid(db, g[2][0])
    with db() as s:
        people.rename(s, a, "Alice")
        dst = people.merge(s, a, b)
        assert dst.id == b and dst.name == "Alice" and dst.face_count == 6
        assert s.get(Person, a) is None
        with pytest.raises(ValueError):
            people.merge(s, b, b)


def test_remove_face_excludes_and_reassigns(db, world) -> None:
    people, _, g = world
    a = _pid(db, g[1][0])
    with db() as s:
        people.remove_face(s, g[1][0])
        assert s.get(Face, g[1][0]).person_id != a
        assert s.get(FaceExclusion, (g[1][0], a)) is not None
        assert s.get(Person, a).face_count == 2


def test_assign_faces_to_new_and_existing(db, world) -> None:
    people, _, g = world
    b = _pid(db, g[2][0])
    with db() as s:
        new_id = people.assign_faces(s, [g[1][0]], None)
        f = s.get(Face, g[1][0])
        assert f.person_id == new_id and f.assigned_by == "user"
        assert people.assign_faces(s, [g[1][1]], b) == b
        assert s.get(Face, g[1][1]).assigned_by == "user"


def test_cover_hidden_and_listing(db, world) -> None:
    people, _, g = world
    a, b = _pid(db, g[1][0]), _pid(db, g[2][0])
    with db() as s:
        assert cover_face_id(s, s.get(Person, a)) == g[1][2]  # best det_score × area
        people.set_cover(s, a, g[1][0])
        assert cover_face_id(s, s.get(Person, a)) == g[1][0]
        with pytest.raises(ValueError):
            people.set_cover(s, a, g[2][0])  # not Alice's face
        people.set_hidden(s, b, True)
        assert [p.id for p, _ in list_people(s)] == [a]
        assert {p.id for p, _ in list_people(s, include_hidden=True)} == {a, b}
        assert person_photos(s, a).total == 3
        assert unsorted_faces(s, people.clusterer.index)[1] == 0
        f = s.get(Face, g[1][0])
        [(face, person)] = file_faces(s, f.file_id)
        assert face.id == f.id and person.id == a
