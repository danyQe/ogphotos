"""Google Takeout JSON sidecars: `<name>.json`, `<name>.supplemental-metadata.json`
(possibly truncated), and `(N)` duplicates like `IMG_1(1).jpg` ↔ `IMG_1.jpg(1).json`.
Takeout writes no sidecar for `X-edited.jpg`; it shares the one of `X.jpg`."""

import json
import re
from datetime import UTC, datetime
from pathlib import PurePosixPath

_NUMBERED_MEDIA = re.compile(r"^(?P<stem>.+)\((?P<n>\d+)\)(?P<ext>\.[^.()]+)$")
_NUMBERED_JSON = re.compile(r"\(\d+\)\.json$", re.IGNORECASE)
_EDITED = re.compile(r"^(?P<stem>.+)-edited(?P<ext>\.[^.]+)$")


def _split(relpath: str) -> tuple[str, str]:
    p = PurePosixPath(relpath.replace("\\", "/"))
    return str(p.parent), p.name


def find_sidecar(media_relpath: str, sidecars: dict[str, bytes]) -> bytes | None:
    """Return the sidecar bytes for a media file, matched within the same directory."""
    media_dir, name = _split(media_relpath)
    in_dir: dict[str, bytes] = {}
    for rel, data in sidecars.items():
        d, n = _split(rel)
        if d == media_dir:
            in_dir[n] = data
    if (data := _match(name, in_dir)) is not None:
        return data
    if m := _EDITED.match(name):
        return _match(f"{m['stem']}{m['ext']}", in_dir)
    return None


def _match(name: str, in_dir: dict[str, bytes]) -> bytes | None:
    if (data := in_dir.get(f"{name}.json")) is not None:
        return data
    for n in sorted(in_dir):
        if (
            n.startswith(f"{name}.")
            and n.lower().endswith(".json")
            and not _NUMBERED_JSON.search(n)
        ):
            return in_dir[n]

    if m := _NUMBERED_MEDIA.match(name):
        base, num = f"{m['stem']}{m['ext']}", m["n"]
        if (data := in_dir.get(f"{base}({num}).json")) is not None:
            return data
        for n in sorted(in_dir):
            if n.startswith(f"{base}.") and n.endswith(f"({num}).json"):
                return in_dir[n]
    return None


def parse_sidecar(data: bytes) -> datetime | None:
    """`photoTakenTime.timestamp` (UTC epoch seconds) as a naive UTC datetime."""
    try:
        ts = int(json.loads(data)["photoTakenTime"]["timestamp"])
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=UTC).replace(tzinfo=None)
    except (ValueError, KeyError, TypeError, OverflowError, OSError):
        return None
