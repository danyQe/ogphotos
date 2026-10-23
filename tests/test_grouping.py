from datetime import datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import File, Group, GroupExclusion
from app.services.grouping import Grouper, add_exclusion, refresh_group
from app.services.phash_index import PhashIndex
from app.services.vector_index import VectorIndex, to_blob

DIM = 4
BASE = datetime(2022, 6, 1, 12, 0)


def _vec(angle_deg: float, plane: int = 0) -> np.ndarray:
    """Unit vector at `angle_deg` from axis `plane`*2, in that axis pair's plane.
    cos between two vectors in one plane = cos(angle difference)."""
    v = np.zeros(DIM, dtype=np.float32)
    a = np.radians(angle_deg)
    v[plane * 2], v[plane * 2 + 1] = np.cos(a), np.sin(a)
    return v


class World:
    """A DB + indexes populated with hand-made files."""

    def __init__(self, db: sessionmaker[Session], settings: Settings) -> None:
        self.db = db
        self.settings = settings.model_copy(update={"embed_dim": DIM})
        self.vectors = VectorIndex(DIM)
        self.phashes = PhashIndex()
        self.grouper = Grouper(self.settings, self.vectors, self.phashes)
        self._n = 0

    def add(
        self,
        vec: np.ndarray | None,
        phash: str | None = None,
        sharp: float = 1.0,
        minutes: int | None = None,
    ) -> int:
        self._n += 1
        with self.db() as s:
            f = File(
                sha256=f"{self._n:064d}",
                phash=phash,
                original_name=f"{self._n}.jpg",
                stored_path=f"library/2022/{self._n}.jpg",
                media_type="image",
                mime="image/jpeg",
                size_bytes=1000,
                width=100,
                height=100,
                taken_at=BASE + timedelta(minutes=self._n if minutes is None else minutes),
                uploaded_at=BASE,
                sharpness=sharp,
                embedding=None if vec is None else to_blob(vec),
                analysis_status="done" if vec is not None else "pending",
            )
            s.add(f)
            s.commit()
            fid = f.id
        if vec is not None:
            self.vectors.add(fid, vec)
        if phash:
            self.phashes.add(fid, phash)
        return fid

    def assign(self, *ids: int) -> list[int | None]:
        with self.db() as s:
            out = [self.grouper.assign(s, i) for i in ids]
            s.commit()
        return out

    def group_of(self, fid: int) -> int | None:
        with self.db() as s:
            return s.get(File, fid).group_id

    def members(self, gid: int) -> set[int]:
        with self.db() as s:
            return set(s.scalars(select(File.id).where(File.group_id == gid)))

    def group(self, gid: int) -> Group:
        with self.db() as s:
            return s.get(Group, gid)

    def exclude(self, a: int, b: int) -> None:
        with self.db() as s:
            add_exclusion(s, a, b)
            s.commit()


@pytest.fixture
def w(db, settings) -> World:
    return World(db, settings)


# cos(10°) = 0.985 (similar), cos(30°) = 0.866 (not similar at 0.92)


def test_lonely_file_gets_no_group(w: World) -> None:
    a = w.add(_vec(0))
    b = w.add(_vec(30))
    assert w.assign(a, b) == [None, None]


def test_second_similar_file_creates_group_and_third_joins(w: World) -> None:
    a = w.add(_vec(0), sharp=1)
    assert w.assign(a) == [None]
    b = w.add(_vec(5), sharp=9)
    [gid] = w.assign(b)
    assert gid is not None and w.members(gid) == {a, b}
    g = w.group(gid)
    assert (g.size, g.best_file_id, g.reviewed) == (2, b, False)

    with w.db() as s:
        s.get(Group, gid).reviewed = True
        s.commit()
    c = w.add(_vec(8), sharp=20)
    assert w.assign(c) == [gid]
    g = w.group(gid)
    assert (g.size, g.best_file_id, g.reviewed) == (3, c, False)  # new member → unreviewed


def test_joins_most_similar_group_and_never_merges(w: World) -> None:
    a, b = w.add(_vec(0)), w.add(_vec(4))
    c, d = w.add(_vec(30)), w.add(_vec(34))  # b↔c 26° → 0.899, not similar
    [g1] = w.assign(b)
    [g2] = w.assign(d)
    assert g1 != g2
    e = w.add(_vec(20))  # similar to a, b, c, d; closest is c (10°, 0.985) vs b (16°, 0.961)
    assert w.assign(e) == [g2]
    assert w.members(g1) == {a, b} and w.members(g2) == {c, d, e}


def test_joining_requires_similarity_to_every_member(w: World) -> None:
    """No chaining: c looks like b, but not like a."""
    a = w.add(_vec(0), sharp=9)
    b = w.add(_vec(15), sharp=1)
    [gid] = w.assign(b)
    assert w.group(gid).best_file_id == a
    c = w.add(_vec(30))  # 15° from b (0.966), 30° from a (0.866)
    assert w.assign(c) == [None]
    assert w.members(gid) == {a, b}


def test_new_group_members_are_all_mutually_similar(w: World) -> None:
    x = w.add(_vec(-15))
    y = w.add(_vec(16))  # both like f, but 31° (0.857) from each other
    f = w.add(_vec(0))
    [gid] = w.assign(f)
    assert w.members(gid) == {f, x}  # x is the closer one
    assert w.group_of(y) is None


def test_assign_is_noop_for_grouped_trashed_or_unknown(w: World) -> None:
    a, b = w.add(_vec(0)), w.add(_vec(3))
    [gid] = w.assign(b)
    assert w.assign(a) == [gid]
    with w.db() as s:
        s.get(File, a).is_trashed = True
        s.get(File, a).group_id = None
        s.commit()
    assert w.assign(a, 999) == [None, None]


def test_phash_alone_is_enough(w: World) -> None:
    a = w.add(_vec(0), phash="0000000000000000")
    b = w.add(_vec(0, plane=1), phash="0000000000000003")  # orthogonal embeddings, 2 bits apart
    [gid] = w.assign(b)
    assert gid is not None and w.members(gid) == {a, b}


def test_pending_neighbor_found_by_phash(w: World) -> None:
    pending = w.add(None, phash="00000000000000ff")
    f = w.add(_vec(0), phash="00000000000000fe")
    [gid] = w.assign(f)
    assert w.members(gid) == {pending, f}


def test_excluded_pair_never_grouped(w: World) -> None:
    a, b = w.add(_vec(0)), w.add(_vec(3))
    w.exclude(b, a)  # order doesn't matter
    assert w.assign(b) == [None]


def test_group_with_excluded_member_is_skipped(w: World) -> None:
    a, b = w.add(_vec(0)), w.add(_vec(4))
    [gid] = w.assign(b)
    c = w.add(_vec(8))  # similar to a and b...
    w.exclude(c, a)  # ...but the user said c is not like a
    assert w.assign(c) == [None]
    assert w.members(gid) == {a, b}


def test_new_group_skips_mutually_excluded_neighbors(w: World) -> None:
    x = w.add(_vec(-3))
    y = w.add(_vec(5))
    w.exclude(x, y)
    f = w.add(_vec(0))
    [gid] = w.assign(f)
    assert w.members(gid) == {f, x}  # x is closer (3° vs 5°); y can't sit with x


def test_refresh_deletes_single_member_groups(w: World) -> None:
    a, b = w.add(_vec(0)), w.add(_vec(3))
    [gid] = w.assign(b)
    with w.db() as s:
        s.get(File, b).is_trashed = True
        refresh_group(s, s.get(Group, gid), w.settings.best_weights())
        s.commit()
    assert w.group(gid) is None
    assert w.group_of(a) is None


def test_backfill_runs_once_in_date_order(w: World) -> None:
    # Analyzed before grouping existed: three burst shots + an outlier, inserted out of order.
    c = w.add(_vec(6), minutes=3)
    a = w.add(_vec(0), minutes=1)
    b = w.add(_vec(3), minutes=2)
    lone = w.add(_vec(60), minutes=4)
    assert w.grouper.backfill(w.db) == 4
    gid = w.group_of(a)
    assert gid is not None and w.members(gid) == {a, b, c}
    assert w.group_of(lone) is None
    later = w.add(_vec(1))
    assert w.grouper.backfill(w.db) == 0  # already done; new files are assigned by the worker
    assert w.group_of(later) is None


def test_add_exclusion_is_idempotent(w: World) -> None:
    a, b = w.add(_vec(0)), w.add(_vec(3))
    w.exclude(a, b)
    w.exclude(b, a)
    with w.db() as s:
        assert len(s.scalars(select(GroupExclusion)).all()) == 1
