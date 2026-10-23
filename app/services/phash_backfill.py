import logging
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import File
from app.services.hashing import phash_hex
from app.services.phash_index import PhashIndex

log = logging.getLogger(__name__)


def backfill_phashes(
    factory: sessionmaker[Session],
    settings: Settings,
    index: PhashIndex,
    stop: threading.Event,
    batch_size: int = 50,
) -> int:
    """Hash stored images that have no pHash yet (uploaded before M2) and index them.

    Commits per batch so uploads aren't blocked for long. Unreadable or missing files
    are logged and left NULL. Returns how many were hashed."""
    done = 0
    last_id = 0
    while not stop.is_set():
        with factory() as session:
            rows = session.scalars(
                select(File)
                .where(
                    File.id > last_id,
                    File.media_type == "image",
                    File.phash.is_(None),
                    File.is_trashed.is_(False),
                )
                .order_by(File.id)
                .limit(batch_size)
            ).all()
            if not rows:
                break
            hashed = []
            for row in rows:
                if stop.is_set():
                    break
                last_id = row.id
                try:
                    row.phash = phash_hex(settings.data_dir / row.stored_path)
                except Exception:
                    log.warning("pHash backfill skipped file %s", row.id, exc_info=True)
                    continue
                hashed.append((row.id, row.phash))
            session.commit()
        for file_id, phash in hashed:
            index.add(file_id, phash)
        done += len(hashed)
    if done:
        log.info("pHash backfill: hashed %d images", done)
    return done
