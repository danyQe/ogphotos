from datetime import datetime
from pathlib import PurePosixPath

from app.models import File

EDITED_SUFFIX = "-edited"  # Google Photos' name for an edited copy (Takeout keeps both)


def is_edited(name: str) -> bool:
    return PurePosixPath(name).stem.lower().endswith(EDITED_SUFFIX)


def _norm(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    return [1.0 if hi == lo else (v - lo) / (hi - lo) for v in values]


def score_members(files: list[File], weights: dict[str, float]) -> dict[int, float]:
    """score = w_sharpness·norm(sharpness) + w_resolution·norm(w·h) + w_size·norm(bytes),
    min-max normalized within the group. Missing values count as 0."""
    columns = {
        "sharpness": [f.sharpness or 0.0 for f in files],
        "resolution": [float((f.width or 0) * (f.height or 0)) for f in files],
        "size": [float(f.size_bytes or 0) for f in files],
    }
    normed = {key: _norm(vals) for key, vals in columns.items()}
    return {
        f.id: sum(weights[key] * normed[key][i] for key in columns) for i, f in enumerate(files)
    }


def rank_members(files: list[File], weights: dict[str, float]) -> list[tuple[File, float]]:
    """Best first. Ties → original over "-edited" copy → earliest taken_at → lowest id."""
    scores = score_members(files, weights)
    return sorted(
        ((f, scores[f.id]) for f in files),
        key=lambda fs: (
            -round(fs[1], 9),
            is_edited(fs[0].original_name),
            fs[0].taken_at or datetime.max,
            fs[0].id,
        ),
    )


def pick_best(files: list[File], weights: dict[str, float]) -> File:
    return rank_members(files, weights)[0][0]
