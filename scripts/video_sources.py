"""Print how stored videos got their dates (sanity check after the video backfill)."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services.locations import resolve_settings  # noqa: E402

db = sqlite3.connect(resolve_settings()[0].db_path)
q = (
    "SELECT taken_at_source, COUNT(*), SUM(duration_s IS NOT NULL) "
    "FROM files WHERE media_type = 'video' GROUP BY 1"
)
for source, n, with_duration in db.execute(q):
    print(f"{source:9} {n:5} videos, {with_duration} with duration")
