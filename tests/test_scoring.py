from datetime import datetime

import pytest

from app.config import Settings
from app.models import File
from app.services.scoring import is_edited, pick_best, score_members

W = Settings().best_weights()


def _f(
    fid: int,
    sharp: float | None = 10.0,
    w: int = 100,
    h: int = 100,
    size: int = 1000,
    name: str = "a.jpg",
    taken: datetime = datetime(2020, 1, 1),
) -> File:
    return File(
        id=fid,
        sharpness=sharp,
        width=w,
        height=h,
        size_bytes=size,
        original_name=name,
        taken_at=taken,
    )


def test_weights_and_min_max_normalization() -> None:
    sharpest = _f(1, sharp=100, w=100, h=100, size=100)
    biggest = _f(2, sharp=0, w=400, h=400, size=900)
    scores = score_members([sharpest, biggest], W)
    assert scores[1] == pytest.approx(0.5)
    assert scores[2] == pytest.approx(0.5)  # 0.3 resolution + 0.2 size
    assert pick_best([sharpest, _f(3, sharp=50)], W).id == 1


def test_equal_values_score_equally() -> None:
    scores = score_members([_f(1), _f(2)], W)
    assert scores[1] == scores[2]


def test_ties_prefer_original_then_earliest() -> None:
    early_edit = _f(1, name="IMG_1-edited.jpg", taken=datetime(2019, 1, 1))
    original = _f(2, name="IMG_1.jpg", taken=datetime(2020, 1, 1))
    assert pick_best([early_edit, original], W).id == 2
    assert (
        pick_best([_f(3, taken=datetime(2021, 1, 1)), _f(4, taken=datetime(2018, 1, 1))], W).id == 4
    )


def test_missing_sharpness_counts_as_lowest() -> None:
    assert pick_best([_f(1, sharp=None), _f(2, sharp=5)], W).id == 2


@pytest.mark.parametrize(
    ("name", "edited"),
    [
        ("IMG_1-edited.jpg", True),
        ("IMG_1-EDITED.JPG", True),
        ("edited.jpg", False),
        ("a.jpg", False),
    ],
)
def test_is_edited(name: str, edited: bool) -> None:
    assert is_edited(name) is edited
