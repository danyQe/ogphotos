"""Trash, restore and Empty Trash (TECH_SPEC §8).

Trashing moves the file from `library/` to `trash/` (same relative path). Empty Trash is the
ONLY code path in the app allowed to delete user files."""

import logging
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Face, FaceExclusion, File, Group, GroupExclusion, Person
from app.services.face_cluster import PeopleClusterer
from app.services.grouping import Grouper, refresh_group
from app.services.vector_index import from_blob

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _swap_root(stored_path: str, root: str) -> str:
    """`library/2021/a.jpg` → `trash/2021/a.jpg` (and back)."""
    p = PurePosixPath(stored_path)
    rest = PurePosixPath(*p.parts[1:]) if len(p.parts) > 1 else p
    return str(PurePosixPath(root) / rest)


def _free_rel(data_dir: Path, rel: str) -> str:
    """`rel`, or `stem-N.ext` if something already lives there. Never overwrite."""
    p = PurePosixPath(rel)
    n = 0
    while (data_dir / rel).exists():
        n += 1
        rel = str(p.with_name(f"{p.stem}-{n}{p.suffix}"))
    return rel


def _move(data_dir: Path, src_rel: str, dest_rel: str) -> str:
    """Move a stored file; returns the relative path it ended up at."""
    src = data_dir / src_rel
    if not src.exists():
        log.warning("stored file missing, updating the record only: %s", src_rel)
        return dest_rel
    dest_rel = _free_rel(data_dir, dest_rel)
    dest = data_dir / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dest)  # same volume; rename never overwrites on Windows, _free_rel elsewhere
    return dest_rel


@dataclass
class Restored:
    file_ids: list[int] = field(default_factory=list)
    needs_analysis: list[int] = field(default_factory=list)  # caller queues these


class Trash:
    """Moves files in and out of `data/trash/`, keeping indexes and groups in step."""

    def __init__(
        self, settings: Settings, grouper: Grouper, clusterer: PeopleClusterer | None = None
    ) -> None:
        self.settings = settings
        self.grouper = grouper
        self.clusterer = clusterer

    def _people_lock(self) -> AbstractContextManager[object]:
        return self.clusterer.lock if self.clusterer else nullcontext()

    def trash(self, session: Session, file_ids: list[int]) -> list[int]:
        """Trash live files; their groups lose them (and dissolve below 2). Commits."""
        data_dir = self.settings.data_dir
        with self.grouper.lock:
            rows = list(
                session.scalars(
                    select(File)
                    .where(File.id.in_(set(file_ids)), File.is_trashed.is_(False))
                    .order_by(File.id)
                )
            )
            moved: list[tuple[str, str]] = []  # (original, new) relative paths
            try:
                now = _utcnow()
                groups: set[int] = set()
                for row in rows:
                    old = row.stored_path
                    row.stored_path = _move(data_dir, old, _swap_root(old, "trash"))
                    moved.append((old, row.stored_path))
                    row.is_trashed = True
                    row.trashed_at = now
                    if row.group_id is not None:
                        groups.add(row.group_id)
                        row.group_id = None
                session.flush()
                weights = self.settings.best_weights()
                for gid in sorted(groups):
                    group = session.get(Group, gid)
                    if group is not None:
                        refresh_group(session, group, weights)
                session.commit()
            except Exception:
                session.rollback()
                for old, new in reversed(moved):
                    if (data_dir / new).exists():
                        _move(data_dir, new, old)
                raise
            for row in rows:
                self.grouper.phashes.remove(row.id)
                self.grouper.vectors.remove(row.id)
            if self.clusterer is not None and rows:
                with self.clusterer.lock:
                    self.clusterer.faces_trashed(session, [r.id for r in rows])
                    session.commit()
        return [row.id for row in rows]

    def restore(self, session: Session, file_ids: list[int]) -> Restored:
        """Move trashed files back, re-index and regroup them. Commits."""
        data_dir = self.settings.data_dir
        out = Restored()
        with self.grouper.lock, self._people_lock():
            rows = list(
                session.scalars(
                    select(File)
                    .where(File.id.in_(set(file_ids)), File.is_trashed.is_(True))
                    .order_by(File.taken_at, File.id)
                )
            )
            moved: list[tuple[str, str]] = []
            try:
                for row in rows:
                    old = row.stored_path
                    row.stored_path = _move(data_dir, old, _swap_root(old, "library"))
                    moved.append((old, row.stored_path))
                    row.is_trashed = False
                    row.trashed_at = None
                    if row.media_type == "image" and row.analysis_status != "done":
                        # e.g. it failed because it was trashed mid-analysis
                        row.analysis_status = "pending"
                        row.analysis_error = None
                        out.needs_analysis.append(row.id)
                    if row.media_type == "image" and row.face_status != "done":
                        row.face_status, row.face_error = "pending", None
                session.commit()
            except Exception:
                session.rollback()
                for old, new in reversed(moved):
                    if (data_dir / new).exists():
                        _move(data_dir, new, old)
                raise
            dim = self.grouper.vectors.dim
            for row in rows:
                out.file_ids.append(row.id)
                if row.phash:
                    self.grouper.phashes.add(row.id, row.phash)
                if row.embedding is not None and len(row.embedding) == dim * 4:
                    self.grouper.vectors.add(row.id, from_blob(row.embedding))
            for row in rows:
                if row.analysis_status == "done":
                    self.grouper.assign(session, row.id)
            if self.clusterer is not None and rows:
                self.clusterer.faces_restored(session, [r.id for r in rows])
            session.commit()
        return out

    def empty(self, session: Session) -> int:
        """Permanently delete every trashed file, its thumbnail and its DB row."""
        trash_root = self.settings.trash_dir.resolve()
        face_ids: list[int] = []
        with self.grouper.lock, self._people_lock():
            rows = list(session.scalars(select(File).where(File.is_trashed.is_(True))))
            if not rows:
                return 0
            ids = [r.id for r in rows]
            paths = [(self.settings.data_dir / r.stored_path).resolve() for r in rows]
            session.execute(
                delete(GroupExclusion).where(
                    or_(GroupExclusion.file_id_a.in_(ids), GroupExclusion.file_id_b.in_(ids))
                )
            )
            session.execute(
                update(Group).where(Group.best_file_id.in_(ids)).values(best_file_id=None)
            )
            face_ids = list(session.scalars(select(Face.id).where(Face.file_id.in_(ids))))
            if face_ids:
                session.execute(delete(FaceExclusion).where(FaceExclusion.face_id.in_(face_ids)))
                session.execute(
                    update(Person)
                    .where(Person.cover_face_id.in_(face_ids))
                    .values(cover_face_id=None)
                )
                session.execute(delete(Face).where(Face.id.in_(face_ids)))
            session.execute(delete(File).where(File.id.in_(ids)))
            session.commit()
        # Rows are gone first: a failed unlink leaves a stray file, never a dangling row.
        for file_id, path in zip(ids, paths, strict=True):
            if path.is_relative_to(trash_root):
                path.unlink(missing_ok=True)
            else:
                log.error("refusing to delete outside trash/: %s", path)
            (self.settings.thumbs_dir / f"{file_id}.jpg").unlink(missing_ok=True)
        for face_id in face_ids:
            (self.settings.faces_dir / f"{face_id}.jpg").unlink(missing_ok=True)
        return len(ids)
