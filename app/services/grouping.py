import logging
import threading
from datetime import UTC, datetime

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import File, Group, GroupExclusion, Setting
from app.services.hashing import hamming
from app.services.phash_index import PhashIndex
from app.services.scoring import pick_best
from app.services.tunables import get_float, get_int
from app.services.vector_index import VectorIndex

log = logging.getLogger(__name__)

# settings-table marker: existing analyzed files have been through grouping once
BACKFILL_KEY = "grouping_backfilled"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def add_exclusion(session: Session, a: int, b: int) -> None:
    """Record that `a` and `b` are "not similar". Stored once, smaller id first."""
    lo, hi = sorted((a, b))
    if session.get(GroupExclusion, (lo, hi)) is None:
        session.add(GroupExclusion(file_id_a=lo, file_id_b=hi))
        session.flush()


def excluded_with(session: Session, file_id: int) -> set[int]:
    rows = session.execute(
        select(GroupExclusion.file_id_a, GroupExclusion.file_id_b).where(
            or_(GroupExclusion.file_id_a == file_id, GroupExclusion.file_id_b == file_id)
        )
    ).all()
    return {b if a == file_id else a for a, b in rows}


def refresh_group(session: Session, group: Group, weights: dict[str, float]) -> None:
    """Recompute size and best; dissolve the group if fewer than 2 live members remain."""
    members = list(
        session.scalars(select(File).where(File.group_id == group.id, File.is_trashed.is_(False)))
    )
    if len(members) < 2:
        for f in session.scalars(select(File).where(File.group_id == group.id)):
            f.group_id = None
        group.best_file_id = None
        session.flush()
        session.delete(group)
        session.flush()
        return
    group.size = len(members)
    group.best_file_id = pick_best(members, weights).id
    group.updated_at = _utcnow()
    session.flush()


class Grouper:
    """Incremental similar-photo grouping (TECH_SPEC §6).

    Similar = pHash distance ≤ phash_max_distance OR cosine ≥ embed_min_cosine, unless the
    pair is excluded. Complete linkage: every pair inside a group is similar, so a file joins
    a group only if it is similar to all of its members. Matching any one member chains
    A≈B≈C≈D (on a real library that produced huge groups containing pairs at cosine 0.79), and
    matching only the best drifts as the best changes. Groups are never merged.

    `lock` serializes every write to group membership (worker, review actions, trash)."""

    def __init__(self, settings: Settings, vectors: VectorIndex, phashes: PhashIndex) -> None:
        self.settings = settings
        self.vectors = vectors
        self.phashes = phashes
        self.lock = threading.RLock()

    def similarity(self, a: File, b: File) -> float:
        """Cosine when both are embedded, else a pHash proxy (1 − bits/64)."""
        va, vb = self.vectors.get(a.id), self.vectors.get(b.id)
        if va is not None and vb is not None:
            return float(va @ vb)
        if a.phash and b.phash:
            return 1.0 - hamming(a.phash, b.phash) / 64
        return 0.0

    def is_similar(self, session: Session, a: File, b: File) -> bool:
        """The grouping predicate for one pair (exclusions checked by the caller)."""
        va, vb = self.vectors.get(a.id), self.vectors.get(b.id)
        min_cos = get_float(session, "embed_min_cosine", self.settings.embed_min_cosine)
        if va is not None and vb is not None and float(va @ vb) >= min_cos:
            return True
        max_d = get_int(session, "phash_max_distance", self.settings.phash_max_distance)
        return bool(a.phash and b.phash and hamming(a.phash, b.phash) <= max_d)

    def neighbors(self, session: Session, f: File) -> dict[int, File]:
        """Live files similar to `f`, excluding pairs the user split apart."""
        limit = self.settings.group_max_neighbors
        ids: set[int] = set()
        vec = self.vectors.get(f.id)
        if vec is not None:
            min_cos = get_float(session, "embed_min_cosine", self.settings.embed_min_cosine)
            ids |= {i for i, _ in self.vectors.search(vec, min_cos, limit, exclude_id=f.id)}
        if f.phash:
            max_d = get_int(session, "phash_max_distance", self.settings.phash_max_distance)
            ids |= {i for i, _ in self.phashes.search(f.phash, max_d, limit)}
        ids -= {f.id} | excluded_with(session, f.id)
        if not ids:
            return {}
        rows = session.scalars(select(File).where(File.id.in_(ids), File.is_trashed.is_(False)))
        return {r.id: r for r in rows}

    def assign(self, session: Session, file_id: int) -> int | None:
        """Place one analyzed file. Returns its group id (or None). Flushes, doesn't commit."""
        f = session.get(File, file_id)
        if f is None or f.is_trashed:
            return None
        if f.group_id is not None:
            return f.group_id
        nbrs = self.neighbors(session, f)
        if not nbrs:
            return None
        sim = {i: self.similarity(f, n) for i, n in nbrs.items()}

        best_group: tuple[float, int] | None = None  # (mean similarity, -group id)
        for gid in {n.group_id for n in nbrs.values() if n.group_id is not None}:
            members = set(
                session.scalars(
                    select(File.id).where(File.group_id == gid, File.is_trashed.is_(False))
                )
            )
            if not members <= nbrs.keys():
                continue  # unlike someone in there (or the user split them apart)
            score = sum(sim[i] for i in members) / len(members)
            if best_group is None or (score, -gid) > best_group:
                best_group = (score, -gid)

        weights = self.settings.best_weights()
        if best_group is not None:
            group = session.get(Group, -best_group[1])
            f.group_id = group.id
            group.reviewed = False
            session.flush()
            refresh_group(session, group, weights)
            return group.id

        # Only ungrouped neighbours: start a group of mutually similar files, closest first.
        joined = [f]
        for n in sorted(
            (n for n in nbrs.values() if n.group_id is None), key=lambda n: (-sim[n.id], n.id)
        ):
            clash = excluded_with(session, n.id)
            if all(
                m is f or (m.id not in clash and self.is_similar(session, n, m)) for m in joined
            ):
                joined.append(n)
        if len(joined) < 2:
            return None
        now = _utcnow()
        group = Group(size=0, reviewed=False, created_at=now, updated_at=now)
        session.add(group)
        session.flush()
        for m in joined:
            m.group_id = group.id
        session.flush()
        refresh_group(session, group, weights)
        return group.id

    def backfill(self, factory: sessionmaker[Session], batch: int = 200) -> int:
        """Group files analyzed before grouping existed, oldest first. Runs once."""
        with factory() as session:
            if session.get(Setting, BACKFILL_KEY) is not None:
                return 0
            ids = session.scalars(
                select(File.id)
                .where(
                    File.analysis_status == "done",
                    File.is_trashed.is_(False),
                    File.group_id.is_(None),
                )
                .order_by(File.taken_at, File.id)
            ).all()
            for start in range(0, len(ids), batch):
                with self.lock:
                    session.expire_all()  # review actions may have changed rows meanwhile
                    for file_id in ids[start : start + batch]:
                        self.assign(session, file_id)
                    session.commit()
            session.add(Setting(key=BACKFILL_KEY, value=_utcnow().isoformat()))
            session.commit()
        if ids:
            log.info("grouping backfill: placed %d analyzed files", len(ids))
        return len(ids)

    def regroup(self, factory: sessionmaker[Session], batch: int = 200) -> int:
        """Throw away every group and rebuild from scratch with the current thresholds.

        Exclusions survive; reviewed flags don't. Returns the number of groups."""
        with self.lock, factory() as session:
            session.execute(update(Group).values(best_file_id=None))
            session.execute(update(File).values(group_id=None))
            session.execute(delete(Group))
            session.commit()
            ids = session.scalars(
                select(File.id)
                .where(File.analysis_status == "done", File.is_trashed.is_(False))
                .order_by(File.taken_at, File.id)
            ).all()
            for start in range(0, len(ids), batch):
                for file_id in ids[start : start + batch]:
                    self.assign(session, file_id)
                session.commit()
            n = session.scalar(select(func.count()).select_from(Group)) or 0
        log.info("regroup: %d files → %d groups", len(ids), n)
        return n
