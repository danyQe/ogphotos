"""One pass over videos stored before thumbnails/durations existed (runs at startup)."""

import logging
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import File
from app.services.ingest import GUESSED_SOURCES
from app.services.thumbnails import make_video_thumbnail
from app.services.video_meta import read_mp4_header

log = logging.getLogger(__name__)


def backfill_videos(
    factory: sessionmaker[Session], settings: Settings, stop: threading.Event
) -> int:
    """Fill duration, a better date and a thumbnail for videos missing them. Only DB rows
    and thumbs/ change; stored files never move. Returns how many videos changed."""
    changed = 0
    last_id = 0
    while not stop.is_set():
        with factory() as session:
            rows = session.scalars(
                select(File)
                .where(File.id > last_id, File.media_type == "video", File.is_trashed.is_(False))
                .order_by(File.id)
                .limit(50)
            ).all()
            if not rows:
                break
            for row in rows:
                if stop.is_set():
                    break
                last_id = row.id
                path = settings.data_dir / row.stored_path
                thumb = settings.thumbs_dir / f"{row.id}.jpg"
                did = False
                if row.duration_s is None:
                    created, duration = read_mp4_header(path)
                    if duration is not None:
                        row.duration_s, did = duration, True
                    if created is not None and row.taken_at_source in GUESSED_SOURCES:
                        row.taken_at, row.taken_at_source, did = created, "metadata", True
                if not thumb.exists() and path.exists():
                    try:
                        made = make_video_thumbnail(path, thumb, settings.thumb_size)
                        did = did or made
                    except Exception:
                        log.warning("video thumbnail failed for file %s", row.id, exc_info=True)
                changed += did
            session.commit()
    if changed:
        log.info("video backfill: updated %d videos", changed)
    return changed
