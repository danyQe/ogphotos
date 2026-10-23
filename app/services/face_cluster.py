"""Puts faces into people (spec §6). All writes to faces.person_id / people go through
PeopleClusterer.lock (an RLock: user actions call assign() while holding it)."""

import logging
import threading
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Face, FaceExclusion, File, Person, Setting
from app.services.face_index import FaceIndex
from app.services.tunables import get_float, get_int
from app.services.vector_index import from_blob

NEXT_LABEL_KEY = "people_next_label"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def person_label(p: Person) -> str:
    return p.name or f"Person {p.label_no}"


class PeopleClusterer:
    def __init__(self, settings: Settings, index: FaceIndex) -> None:
        self.settings = settings
        self.index = index
        self.lock = threading.RLock()

    def _thresholds(self, session: Session) -> tuple[float, int]:
        s = self.settings
        return (
            get_float(session, "face_match_cosine", s.face_match_cosine),
            get_int(session, "face_min_cluster", s.face_min_cluster),
        )

    def new_person(self, session: Session) -> Person:
        row = session.get(Setting, NEXT_LABEL_KEY)
        if row is None:
            highest = session.scalar(select(func.max(Person.label_no))) or 0
            row = Setting(key=NEXT_LABEL_KEY, value=str(highest + 1))
            session.add(row)
        n = int(row.value)
        row.value = str(n + 1)
        now = _utcnow()
        person = Person(label_no=n, face_count=0, hidden=False, created_at=now, updated_at=now)
        session.add(person)
        session.flush()
        return person

    def refresh(self, session: Session, person_ids: Iterable[int]) -> None:
        session.flush()
        now = _utcnow()
        for pid in set(person_ids):
            person = session.get(Person, pid)
            if person is None:
                continue
            person.face_count = (
                session.scalar(
                    select(func.count(Face.id))
                    .join(File, File.id == Face.file_id)
                    .where(Face.person_id == pid, File.is_trashed.is_(False))
                )
                or 0
            )
            person.updated_at = now
        session.flush()

    def faces_trashed(self, session: Session, file_ids: list[int]) -> None:
        """Faces on trashed files leave the index and the counts (rows are kept)."""
        rows = session.execute(
            select(Face.id, Face.person_id).where(Face.file_id.in_(file_ids))
        ).all()
        for face_id, _ in rows:
            self.index.remove(face_id)
        self.refresh(session, {pid for _, pid in rows if pid is not None})

    def faces_restored(self, session: Session, file_ids: list[int]) -> None:
        """Faces on restored files rejoin the index and the counts."""
        rows = session.execute(
            select(Face.id, Face.person_id, Face.embedding).where(Face.file_id.in_(file_ids))
        ).all()
        for face_id, _, blob in rows:
            vec = from_blob(blob)
            if vec.shape == (self.index.dim,):
                self.index.add(face_id, vec)
        self.refresh(session, {pid for _, pid, _ in rows if pid is not None})

    def assign(self, session: Session, face_id: int, create: bool = True) -> int | None:
        """Place one face: join the person most of its close neighbours belong to, or start
        a new person once enough similar unassigned faces exist. Flushes."""
        face = session.get(Face, face_id)
        if face is None or face.person_id is not None:
            return face.person_id if face else None
        vec = self.index.get(face_id)
        if vec is None:
            return None
        threshold, min_cluster = self._thresholds(session)
        hits = self.index.search(vec, threshold, self.settings.face_neighbors, exclude_id=face_id)
        if not hits:
            return None
        owner = dict(
            session.execute(
                select(Face.id, Face.person_id).where(Face.id.in_([h for h, _ in hits]))
            ).all()
        )
        excluded = set(
            session.scalars(select(FaceExclusion.person_id).where(FaceExclusion.face_id == face_id))
        )
        votes: dict[int, list[float]] = defaultdict(list)
        unassigned: list[int] = []
        for fid, cos in hits:
            pid = owner.get(fid)
            if pid is None:
                unassigned.append(fid)
            elif pid not in excluded:
                votes[pid].append(cos)
        if votes:
            best = max(votes, key=lambda p: (len(votes[p]), sum(votes[p]) / len(votes[p]), -p))
            face.person_id, face.assigned_by = best, "auto"
            self.refresh(session, [best])
            return best
        if not create or len(unassigned) + 1 < min_cluster:
            return None
        person = self.new_person(session)
        for fid in [face_id, *unassigned]:
            member = session.get(Face, fid)
            if member is not None:
                member.person_id, member.assigned_by = person.id, "auto"
        self.refresh(session, [person.id])
        return person.id

    def _dbscan(self, session: Session, face_ids: list[int]) -> list[list[int]]:
        threshold, min_cluster = self._thresholds(session)
        pool = set(face_ids)
        neighbours: dict[int, list[int]] = {}
        for fid in face_ids:
            vec = self.index.get(fid)
            hits = self.index.search(vec, threshold, len(self.index), exclude_id=fid)
            neighbours[fid] = [h for h, _ in hits if h in pool]
        core = {f for f in face_ids if len(neighbours[f]) + 1 >= min_cluster}
        seen: set[int] = set()
        clusters: list[list[int]] = []
        for start in face_ids:
            if start not in core or start in seen:
                continue
            members, stack = [start], [start]
            seen.add(start)
            while stack:
                for n in neighbours[stack.pop()]:
                    if n not in seen:
                        seen.add(n)
                        members.append(n)
                        if n in core:
                            stack.append(n)
            clusters.append(sorted(members))
        return clusters

    def recluster(self, factory: sessionmaker[Session]) -> int:
        """Rebuild automatic assignments with the current thresholds (spec §6). Faces the
        user placed and faces of named people never move. Returns people with faces."""
        with self.lock, factory() as session:
            named = set(session.scalars(select(Person.id).where(Person.name.is_not(None))))
            live_files = select(File.id).where(File.is_trashed.is_(False))
            old = dict(
                session.execute(
                    select(Face.id, Face.person_id).where(
                        Face.assigned_by == "auto",
                        Face.person_id.not_in(named),
                        Face.file_id.in_(live_files),
                    )
                ).all()
            )
            session.execute(
                update(Face)
                .where(
                    Face.file_id.in_(live_files),
                    Face.assigned_by.is_(None) | (Face.assigned_by == "auto"),
                    or_(Face.person_id.is_(None), Face.person_id.not_in(named)),
                )
                .values(person_id=None, assigned_by=None)
            )
            session.expire_all()
            live = set(self.index.ids())
            loose = sorted(
                f
                for f in session.scalars(select(Face.id).where(Face.person_id.is_(None)))
                if f in live
            )
            for fid in loose:
                self.assign(session, fid, create=False)
            rest = [f for f in loose if session.get(Face, f).person_id is None]
            reused: set[int] = set()
            for members in self._dbscan(session, rest):
                counts = Counter(old[f] for f in members if old.get(f) is not None)
                pid = None
                if counts:
                    cand, n = counts.most_common(1)[0]
                    if n * 2 > len(members) and cand not in reused and session.get(Person, cand):
                        pid = cand
                if pid is None:
                    pid = self.new_person(session).id
                reused.add(pid)
                excluded = set(
                    session.scalars(
                        select(FaceExclusion.face_id).where(FaceExclusion.person_id == pid)
                    )
                )
                for fid in members:
                    if fid not in excluded:
                        face = session.get(Face, fid)
                        face.person_id, face.assigned_by = pid, "auto"
            session.flush()
            empty = list(
                session.scalars(
                    select(Person.id).where(
                        Person.name.is_(None), ~exists().where(Face.person_id == Person.id)
                    )
                )
            )
            if empty:
                session.execute(delete(FaceExclusion).where(FaceExclusion.person_id.in_(empty)))
                session.execute(delete(Person).where(Person.id.in_(empty)))
            self.refresh(session, session.scalars(select(Person.id)).all())
            session.commit()
            return session.scalar(select(func.count(Person.id)).where(Person.face_count > 0)) or 0


log = logging.getLogger(__name__)


@dataclass
class ReclusterState:
    status: str = "idle"  # idle | running | done | failed
    people: int | None = None
    error: str | None = None
    finished_at: datetime | None = None


class ReclusterJob:
    def __init__(self, factory: sessionmaker[Session], clusterer: PeopleClusterer) -> None:
        self.factory = factory
        self.clusterer = clusterer
        self.state = ReclusterState()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if self.state.status == "running":
            return False
        self.state = ReclusterState(status="running")
        self._thread = threading.Thread(target=self._run, name="recluster", daemon=True)
        self._thread.start()
        return True

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        try:
            self.state.people = self.clusterer.recluster(self.factory)
            self.state.status = "done"
        except Exception as e:
            log.exception("recluster failed")
            self.state.status, self.state.error = "failed", str(e)
        finally:
            self.state.finished_at = _utcnow()
