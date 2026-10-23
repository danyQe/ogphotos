"""Group review actions: keep best, apply a keep/trash selection, split out, mark reviewed."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import File, Group
from app.services.grouping import add_exclusion, refresh_group
from app.services.trash import Trash


class GroupNotFound(LookupError):
    pass


class NotAMember(ValueError):
    pass


def _group(session: Session, group_id: int) -> Group:
    group = session.get(Group, group_id)
    if group is None:
        raise GroupNotFound(f"group {group_id} not found")
    return group


def _member_ids(session: Session, group_id: int) -> list[int]:
    return list(
        session.scalars(
            select(File.id)
            .where(File.group_id == group_id, File.is_trashed.is_(False))
            .order_by(File.id)
        )
    )


def keep_best(session: Session, trash: Trash, group_id: int) -> list[int]:
    """Trash every member except the suggested best. Returns trashed ids."""
    with trash.grouper.lock:
        group = _group(session, group_id)
        group.reviewed = True
        others = [i for i in _member_ids(session, group_id) if i != group.best_file_id]
        if not others:
            session.commit()
            return []
        return trash.trash(session, others)


def apply_selection(
    session: Session, trash: Trash, group_id: int, keep: list[int], to_trash: list[int]
) -> list[int]:
    """Trash `to_trash`, keep the rest, mark the group reviewed. Ids must be members."""
    with trash.grouper.lock:
        group = _group(session, group_id)
        members = set(_member_ids(session, group_id))
        strangers = (set(keep) | set(to_trash)) - members
        if strangers:
            raise NotAMember(f"not in group {group_id}: {sorted(strangers)}")
        if set(keep) & set(to_trash):
            raise NotAMember("a photo cannot be both kept and trashed")
        group.reviewed = True
        if not to_trash:
            session.commit()
            return []
        return trash.trash(session, to_trash)


def split_out(session: Session, trash: Trash, group_id: int, file_id: int) -> int | None:
    """Not similar: take `file_id` out and never re-join it with the others.

    The file is then placed again (it may join or start another group). Returns its new
    group id. The old group dissolves if fewer than 2 photos remain."""
    grouper = trash.grouper
    with grouper.lock:
        group = _group(session, group_id)
        members = _member_ids(session, group_id)
        if file_id not in members:
            raise NotAMember(f"file {file_id} is not in group {group_id}")
        for other in members:
            if other != file_id:
                add_exclusion(session, file_id, other)
        session.get(File, file_id).group_id = None
        session.flush()
        refresh_group(session, group, grouper.settings.best_weights())
        new_gid = grouper.assign(session, file_id)
        session.commit()
        return new_gid


def mark_reviewed(session: Session, group_id: int, reviewed: bool = True) -> None:
    _group(session, group_id).reviewed = reviewed
    session.commit()
