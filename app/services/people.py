"""People page actions (spec §7) and queries. Every write holds PeopleClusterer.lock."""

from typing import Any

import numpy as np
from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from app.models import Face, FaceExclusion, File, Person
from app.services.face_cluster import PeopleClusterer, person_label
from app.services.face_index import FaceIndex
from app.services.library import Page

NAME_MAX = 100


def _live_faces() -> Select[tuple[Face]]:
    """Faces on non-trashed files."""
    return select(Face).join(File, File.id == Face.file_id).where(File.is_trashed.is_(False))


def _person(session: Session, person_id: int) -> Person:
    p = session.get(Person, person_id)
    if p is None:
        raise LookupError("person not found")
    return p


def _face(session: Session, face_id: int) -> Face:
    f = session.get(Face, face_id)
    if f is None:
        raise LookupError("face not found")
    return f


def cover_face_id(session: Session, person: Person) -> int | None:
    """The chosen cover if still valid, else the clearest, biggest live face."""
    if person.cover_face_id is not None:
        ok = session.scalar(
            _live_faces()
            .with_only_columns(Face.id)
            .where(Face.id == person.cover_face_id, Face.person_id == person.id)
        )
        if ok is not None:
            return ok
    return session.scalar(
        _live_faces()
        .with_only_columns(Face.id)
        .where(Face.person_id == person.id)
        .order_by((Face.det_score * Face.box_w * Face.box_h).desc(), Face.id)
        .limit(1)
    )


class People:
    def __init__(self, clusterer: PeopleClusterer) -> None:
        self.clusterer = clusterer

    def rename(self, session: Session, person_id: int, name: str) -> list[int]:
        with self.clusterer.lock:
            p = _person(session, person_id)
            p.name = name.strip()[:NAME_MAX] or None
            session.commit()
            if p.name is None:
                return []
            return list(
                session.scalars(select(Person.id).where(Person.name == p.name, Person.id != p.id))
            )

    def set_hidden(self, session: Session, person_id: int, hidden: bool) -> None:
        with self.clusterer.lock:
            _person(session, person_id).hidden = hidden
            session.commit()

    def set_cover(self, session: Session, person_id: int, face_id: int) -> None:
        with self.clusterer.lock:
            p = _person(session, person_id)
            if _face(session, face_id).person_id != p.id:
                raise ValueError("that face is not this person")
            p.cover_face_id = face_id
            session.commit()

    def merge(self, session: Session, src_id: int, dst_id: int) -> Person:
        if src_id == dst_id:
            raise ValueError("cannot merge a person into themselves")
        with self.clusterer.lock:
            src, dst = _person(session, src_id), _person(session, dst_id)
            for face in session.scalars(select(Face).where(Face.person_id == src.id)):
                face.person_id = dst.id
            for ex in session.scalars(
                select(FaceExclusion).where(FaceExclusion.person_id == src.id)
            ):
                if session.get(FaceExclusion, (ex.face_id, dst.id)) is None:
                    session.add(FaceExclusion(face_id=ex.face_id, person_id=dst.id))
            session.flush()
            session.execute(delete(FaceExclusion).where(FaceExclusion.person_id == src.id))
            # a face the user merged in is no longer "not this person"
            session.execute(
                delete(FaceExclusion).where(
                    FaceExclusion.person_id == dst.id,
                    FaceExclusion.face_id.in_(select(Face.id).where(Face.person_id == dst.id)),
                )
            )
            dst.name = dst.name or src.name
            dst.cover_face_id = dst.cover_face_id or src.cover_face_id
            session.delete(src)
            self.clusterer.refresh(session, [dst.id])
            session.commit()
            return dst

    def remove_face(self, session: Session, face_id: int) -> int | None:
        with self.clusterer.lock:
            face = _face(session, face_id)
            old = face.person_id
            if old is None:
                raise ValueError("face is not assigned to anyone")
            face.person_id = face.assigned_by = None
            if session.get(FaceExclusion, (face.id, old)) is None:
                session.add(FaceExclusion(face_id=face.id, person_id=old))
            person = session.get(Person, old)
            if person is not None and person.cover_face_id == face.id:
                person.cover_face_id = None
            self.clusterer.refresh(session, [old])
            new = self.clusterer.assign(session, face.id)
            session.commit()
            return new

    def assign_faces(self, session: Session, face_ids: list[int], person_id: int | None) -> int:
        if not face_ids:
            raise ValueError("no faces given")
        with self.clusterer.lock:
            faces = [_face(session, i) for i in face_ids]
            target = (
                self.clusterer.new_person(session)
                if person_id is None
                else _person(session, person_id)
            )
            touched = {target.id}
            for face in faces:
                if face.person_id is not None and face.person_id != target.id:
                    touched.add(face.person_id)
                    old = session.get(Person, face.person_id)
                    if old is not None and old.cover_face_id == face.id:
                        old.cover_face_id = None
                face.person_id, face.assigned_by = target.id, "user"
            session.execute(
                delete(FaceExclusion).where(
                    FaceExclusion.person_id == target.id, FaceExclusion.face_id.in_(face_ids)
                )
            )
            self.clusterer.refresh(session, touched)
            session.commit()
            return target.id


def list_people(session: Session, include_hidden: bool = False) -> list[tuple[Person, int | None]]:
    stmt = select(Person).where(Person.face_count > 0)
    if not include_hidden:
        stmt = stmt.where(Person.hidden.is_(False))
    rows = session.scalars(stmt.order_by(Person.face_count.desc(), Person.label_no)).all()
    return [(p, cover_face_id(session, p)) for p in rows]


def person_photos(session: Session, person_id: int, page: int = 1, per_page: int = 120) -> Page:
    ids = select(Face.file_id).where(Face.person_id == person_id).distinct().scalar_subquery()
    where = (File.id.in_(ids), File.is_trashed.is_(False))
    total = session.scalar(select(func.count(File.id)).where(*where)) or 0
    files = session.scalars(
        select(File)
        .where(*where)
        .order_by(File.taken_at.desc(), File.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    return Page(total=total, page=page, per_page=per_page, files=list(files))


def person_faces(
    session: Session, person_id: int, page: int = 1, per_page: int = 120
) -> tuple[list[Face], int]:
    stmt = _live_faces().where(Face.person_id == person_id)
    total = session.scalar(stmt.with_only_columns(func.count(Face.id))) or 0
    rows = session.scalars(
        stmt.order_by(Face.id).offset((page - 1) * per_page).limit(per_page)
    ).all()
    return list(rows), total


def unsorted_faces(
    session: Session, index: FaceIndex, page: int = 1, per_page: int = 120
) -> tuple[list[Face], int]:
    """Unassigned live faces by id; within the page, similar faces placed side by side."""
    stmt = _live_faces().where(Face.person_id.is_(None))
    total = session.scalar(stmt.with_only_columns(func.count(Face.id))) or 0
    rows = list(
        session.scalars(stmt.order_by(Face.id).offset((page - 1) * per_page).limit(per_page))
    )
    vecs = {f.id: index.get(f.id) for f in rows}
    with_vec = [f for f in rows if vecs[f.id] is not None]
    ordered, left = with_vec[:1], with_vec[1:]
    while left:  # greedy: next = most similar to the last one placed
        last = vecs[ordered[-1].id]
        nxt = max(left, key=lambda f: float(np.dot(vecs[f.id], last)))
        left.remove(nxt)
        ordered.append(nxt)
    return ordered + [f for f in rows if vecs[f.id] is None], total


def file_faces(session: Session, file_id: int) -> list[tuple[Face, Person | None]]:
    faces = session.scalars(select(Face).where(Face.file_id == file_id).order_by(Face.box_x)).all()
    return [(f, session.get(Person, f.person_id) if f.person_id else None) for f in faces]


def person_json(p: Person, cover: int | None) -> dict[str, Any]:
    return {
        "person_id": p.id,
        "label_no": p.label_no,
        "name": p.name,
        "label": person_label(p),
        "face_count": p.face_count,
        "hidden": p.hidden,
        "cover_face_id": cover,
    }


def face_json(f: Face) -> dict[str, Any]:
    return {
        "face_id": f.id,
        "file_id": f.file_id,
        "person_id": f.person_id,
        "box": [f.box_x, f.box_y, f.box_w, f.box_h],
        "det_score": f.det_score,
        "assigned_by": f.assigned_by,
    }
