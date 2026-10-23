from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Setting


def get_int(session: Session, key: str, default: int) -> int:
    """User-adjustable value from the settings table; `default` if missing or malformed."""
    row = session.get(Setting, key)
    try:
        return int(row.value) if row else default
    except ValueError:
        return default


def get_float(session: Session, key: str, default: float) -> float:
    row = session.get(Setting, key)
    try:
        return float(row.value) if row else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Tunable:
    key: str
    kind: type[int] | type[float]
    lo: float
    hi: float
    label: str
    section: str = "similarity"

    @property
    def step(self) -> float:
        return 1 if self.kind is int else 0.001


# Values the Settings page may change (bounds are sanity limits, not recommendations)
TUNABLES = (
    Tunable("phash_max_distance", int, 0, 32, "pHash max distance (bits of 64)"),
    Tunable("embed_min_cosine", float, 0.5, 0.999, "Embedding min cosine"),
    Tunable("face_min_score", float, 0.1, 0.99, "Min face detector confidence", "faces"),
    Tunable("face_min_px", int, 16, 512, "Min face size (pixels, short side)", "faces"),
    Tunable("face_match_cosine", float, 0.2, 0.9, "Same-person min cosine", "faces"),
    Tunable("face_min_cluster", int, 2, 20, "Faces needed to start a person", "faces"),
)


def current(session: Session, defaults: dict[str, str]) -> dict[str, int | float]:
    out: dict[str, int | float] = {}
    for t in TUNABLES:
        get = get_int if t.kind is int else get_float
        out[t.key] = get(session, t.key, t.kind(defaults[t.key]))
    return out


def update_tunables(session: Session, values: dict[str, object]) -> dict[str, int | float]:
    """Validate and store; raises ValueError naming the bad key. Commits."""
    known = {t.key: t for t in TUNABLES}
    parsed: dict[str, int | float] = {}
    for key, raw in values.items():
        t = known.get(key)
        if t is None:
            raise ValueError(f"unknown setting: {key}")
        try:
            if t.kind is int and isinstance(raw, float) and not raw.is_integer():
                raise ValueError
            v = t.kind(raw)  # type: ignore[call-overload]
        except (TypeError, ValueError):
            raise ValueError(f"{key}: expected a number") from None
        if not t.lo <= v <= t.hi:
            raise ValueError(f"{key}: must be between {t.lo:g} and {t.hi:g}")
        parsed[key] = v
    for key, v in parsed.items():
        row = session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=str(v)))
        else:
            row.value = str(v)
    session.commit()
    return parsed
