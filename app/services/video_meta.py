"""Creation time and duration from the MP4/MOV `moov/mvhd` box (plain Python, no ffmpeg)."""

import struct
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO

MAC_EPOCH = datetime(1904, 1, 1)  # QuickTime/MP4 times count UTC seconds from here
MIN_YEAR = 1990  # 0 / 1904 / 1970 = the camera clock was never set


def _boxes(f: BinaryIO, end: int) -> Iterator[tuple[bytes, int, int]]:
    """(type, payload start, box end) of each box from the current position to `end`."""
    while f.tell() + 8 <= end:
        start = f.tell()
        size, kind = struct.unpack(">I4s", f.read(8))
        header = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            header = 16
        elif size == 0:
            size = end - start
        if size < header or start + size > end:
            return
        yield kind, start + header, start + size
        f.seek(start + size)


def read_mp4_header(path: Path) -> tuple[datetime | None, float | None]:
    """(creation time as naive UTC, duration in seconds); (None, None) if unreadable."""
    try:
        with path.open("rb") as f:
            for kind, start, stop in _boxes(f, path.stat().st_size):
                if kind != b"moov":
                    continue
                f.seek(start)
                for sub, s_start, _ in _boxes(f, stop):
                    if sub != b"mvhd":
                        continue
                    f.seek(s_start)
                    version = f.read(4)[0]
                    if version == 1:
                        created, _, scale, dur = struct.unpack(">QQIQ", f.read(28))
                    else:
                        created, _, scale, dur = struct.unpack(">IIII", f.read(16))
                    when = MAC_EPOCH + timedelta(seconds=created) if created else None
                    if when is not None and when.year < MIN_YEAR:
                        when = None
                    return when, (dur / scale if scale else None)
                return None, None
    except (OSError, struct.error, IndexError, OverflowError):
        pass
    return None, None
