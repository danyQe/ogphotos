from sqlalchemy import select

from app.config import Settings
from app.models import Face, FaceExclusion, Person
from app.services.face_cluster import PeopleClusterer, person_label
from app.services.face_index import FaceIndex
from tests.faceutil import add_face, face_vec


def _setup(db, settings: Settings):
    return PeopleClusterer(settings, FaceIndex(512))


def _faces(s, c, person: int, photos: range) -> list[Face]:
    out = []
    for p in photos:
        f = add_face(s, c.index, face_vec(person, p))
        c.assign(s, f.id)
        out.append(f)
    return out


def test_person_created_at_min_cluster(db, settings: Settings) -> None:
    c = _setup(db, settings)
    with db() as s:
        a = _faces(s, c, 1, range(2))
        assert all(f.person_id is None for f in a)  # 2 < face_min_cluster (3)
        a += _faces(s, c, 1, range(2, 3))
        pids = {f.person_id for f in a}
        assert len(pids) == 1 and None not in pids
        person = s.get(Person, pids.pop())
        assert person.label_no == 1 and person_label(person) == "Person 1"
        assert person.face_count == 3 and all(f.assigned_by == "auto" for f in a)


def test_new_face_joins_existing_person_by_vote(db, settings: Settings) -> None:
    c = _setup(db, settings)
    with db() as s:
        alice = _faces(s, c, 1, range(3))
        bob = _faces(s, c, 2, range(3))
        assert alice[0].person_id != bob[0].person_id
        late = _faces(s, c, 1, range(3, 4))[0]
        assert late.person_id == alice[0].person_id
        assert s.get(Person, bob[0].person_id).label_no == 2


def test_bridge_face_does_not_merge_two_people(db, settings: Settings) -> None:
    c = _setup(db, settings)
    with db() as s:
        alice = _faces(s, c, 1, range(3))
        bob = _faces(s, c, 2, range(3))
        mix = face_vec(1, 9) + face_vec(2, 9)
        bridge = add_face(s, c.index, mix / ((mix @ mix) ** 0.5))
        c.assign(s, bridge.id)
        assert bridge.person_id in {alice[0].person_id, bob[0].person_id}
        assert {f.person_id for f in alice} != {f.person_id for f in bob}


def test_exclusion_is_honoured(db, settings: Settings) -> None:
    c = _setup(db, settings)
    with db() as s:
        alice = _faces(s, c, 1, range(3))
        f = add_face(s, c.index, face_vec(1, 5))
        s.add(FaceExclusion(face_id=f.id, person_id=alice[0].person_id))
        s.flush()
        assert c.assign(s, f.id) is None and f.person_id is None


def test_label_numbers_never_reused(db, settings: Settings) -> None:
    c = _setup(db, settings)
    with db() as s:
        p1 = c.new_person(s)
        s.delete(p1)
        s.flush()
        assert c.new_person(s).label_no == 2


def test_different_people_stay_unassigned(db, settings: Settings) -> None:
    c = _setup(db, settings)
    with db() as s:
        for person in range(1, 6):
            _faces(s, c, person, range(1))
        assert s.scalars(select(Person)).all() == []
