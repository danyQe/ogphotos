"""Read models for the groups pages and API."""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import File, Group
from app.services.scoring import rank_members

PREVIEW_SIZE = 6


@dataclass
class GroupSummary:
    id: int
    size: int
    reviewed: bool
    best_file_id: int | None
    date_from: datetime | None
    date_to: datetime | None
    preview_file_ids: list[int] = field(default_factory=list)


@dataclass
class Member:
    file: File
    score: float
    is_best: bool


@dataclass
class GroupDetail:
    group: Group
    members: list[Member]
    prev_id: int | None
    next_id: int | None


def list_groups(
    session: Session, reviewed: bool | None = None, year: int | None = None
) -> list[GroupSummary]:
    """Largest first (then oldest id). `year` matches the group's earliest photo."""
    groups = {g.id: g for g in session.scalars(select(Group))}
    members: dict[int, list[tuple[int, datetime | None]]] = {gid: [] for gid in groups}
    for fid, gid, taken in session.execute(
        select(File.id, File.group_id, File.taken_at).where(
            File.group_id.is_not(None), File.is_trashed.is_(False)
        )
    ):
        members.setdefault(gid, []).append((fid, taken))

    out = []
    for gid, g in groups.items():
        rows = sorted(members.get(gid, []), key=lambda r: (r[1] or datetime.max, r[0]))
        dates = [t for _, t in rows if t is not None]
        summary = GroupSummary(
            id=gid,
            size=g.size,
            reviewed=g.reviewed,
            best_file_id=g.best_file_id,
            date_from=min(dates) if dates else None,
            date_to=max(dates) if dates else None,
            preview_file_ids=([g.best_file_id] if g.best_file_id else [])
            + [fid for fid, _ in rows if fid != g.best_file_id],
        )
        summary.preview_file_ids = summary.preview_file_ids[:PREVIEW_SIZE]
        if reviewed is not None and g.reviewed != reviewed:
            continue
        if year is not None and (summary.date_from is None or summary.date_from.year != year):
            continue
        out.append(summary)
    out.sort(key=lambda s: (-s.size, s.id))
    return out


def group_detail(session: Session, group_id: int, weights: dict[str, float]) -> GroupDetail | None:
    group = session.get(Group, group_id)
    if group is None:
        return None
    files = list(
        session.scalars(select(File).where(File.group_id == group_id, File.is_trashed.is_(False)))
    )
    ranked = rank_members(files, weights) if files else []
    # The stored best wins (it is what list views show); scores explain the ranking.
    ranked.sort(key=lambda fs: fs[0].id != group.best_file_id)
    prev_id, next_id = prev_next(session, group_id)
    return GroupDetail(
        group=group,
        members=[Member(f, score, f.id == group.best_file_id) for f, score in ranked],
        prev_id=prev_id,
        next_id=next_id,
    )


def prev_next(session: Session, group_id: int) -> tuple[int | None, int | None]:
    """Neighbours of a group in list order (largest first)."""
    order = [s.id for s in list_groups(session)]
    i = order.index(group_id) if group_id in order else -1
    return (
        order[i - 1] if i > 0 else None,
        order[i + 1] if 0 <= i < len(order) - 1 else None,
    )
