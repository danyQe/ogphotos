"""Background face scanning (spec §5). Polls the DB for `face_status = pending` images,
so uploads, imports, restores and the first-run backfill need no extra wiring."""

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Face, File, Setting
from app.services.face_cluster import PeopleClusterer
from app.services.face_engine import INSTALL_HINT, FaceEngine, FaceModelUnavailableError
from app.services.tunables import get_float, get_int
from app.services.vector_index import to_blob

log = logging.getLogger(__name__)

STATUSES = ("pending", "done", "failed")
SCAN_KEY = "face_scan_enabled"
IDLE_WAIT = 2.0
ERROR_WAIT = 5.0
CROP_MARGIN = 1.4  # crop side = 1.4 × the longer box side


def load_for_faces(path: Path, max_side: int) -> tuple[Image.Image, float]:
    """Upright RGB image with long edge ≤ max_side, and its scale vs. the original."""
    with Image.open(path) as img:
        original_long = max(img.size)
        img.draft("RGB", (max_side, max_side))
        upright = ImageOps.exif_transpose(img).convert("RGB")
    upright.thumbnail((max_side, max_side))
    return upright, max(upright.size) / original_long


def save_crop(
    img: Image.Image, box: tuple[float, float, float, float], dest: Path, size: int
) -> None:
    x, y, w, h = box
    side = max(w, h) * CROP_MARGIN
    left, top = x + w / 2 - side / 2, y + h / 2 - side / 2
    crop = img.crop((round(left), round(top), round(left + side), round(top + side)))
    crop.resize((size, size), Image.Resampling.LANCZOS).save(dest, "JPEG", quality=85)


def face_counts(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(File.face_status, func.count())
        .where(File.media_type == "image", File.is_trashed.is_(False))
        .group_by(File.face_status)
    ).all()
    counts = dict.fromkeys(STATUSES, 0)
    counts.update({status: n for status, n in rows if status in counts})
    return counts


def scanning_enabled(session: Session, settings: Settings) -> bool:
    row = session.get(Setting, SCAN_KEY)
    return settings.face_enabled if row is None else row.value == "1"


def set_scanning(session: Session, enabled: bool) -> None:
    row = session.get(Setting, SCAN_KEY)
    if row is None:
        session.add(Setting(key=SCAN_KEY, value="1" if enabled else "0"))
    else:
        row.value = "1" if enabled else "0"
    session.commit()


def retry_failed(session: Session) -> int:
    n = session.execute(
        update(File)
        .where(File.face_status == "failed", File.is_trashed.is_(False))
        .values(face_status="pending", face_error=None)
    ).rowcount
    session.commit()
    return n


class FaceWorker:
    def __init__(
        self,
        factory: sessionmaker[Session],
        settings: Settings,
        engine: FaceEngine,
        clusterer: PeopleClusterer,
    ) -> None:
        self.factory = factory
        self.settings = settings
        self.engine = engine
        self.clusterer = clusterer
        self.error: str | None = None
        self._busy = threading.Lock()  # one batch at a time (thread vs. run_pending)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="faces", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _ready(self) -> bool:
        with self.factory() as session:
            if not scanning_enabled(session, self.settings):
                return False
        if not self.engine.available():
            self.error = f"face model not installed — {INSTALL_HINT}"
            return False
        return True

    def next_pending(self, limit: int) -> list[int]:
        with self.factory() as session:
            return list(
                session.scalars(
                    select(File.id)
                    .where(
                        File.face_status == "pending",
                        File.media_type == "image",
                        File.is_trashed.is_(False),
                    )
                    .order_by(File.id)
                    .limit(limit)
                )
            )

    def run_pending(self) -> int:
        """Scan everything pending now (tests, scripts). Returns files scanned."""
        total = 0
        while self._ready() and (ids := self.next_pending(self.settings.face_batch_size)):
            before = len(ids)
            try:
                self.process(ids)
            except FaceModelUnavailableError as e:
                self.error = str(e)
                break
            total += before
        with self._busy:  # wait for a batch the background thread is still clustering
            pass
        return total

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._ready():
                self._stop.wait(ERROR_WAIT)
                continue
            ids = self.next_pending(self.settings.face_batch_size)
            if not ids:
                self._stop.wait(IDLE_WAIT)
                continue
            try:
                self.process(ids)
                self.error = None
            except FaceModelUnavailableError as e:
                log.error("%s", e)
                self.error = str(e)
                self._stop.wait(ERROR_WAIT)
            except Exception:
                log.exception("face batch failed: %s", ids)
                self._stop.wait(ERROR_WAIT)

    def process(self, file_ids: list[int]) -> None:
        s = self.settings
        with self._busy:
            new: list[tuple[Face, np.ndarray]] = []
            with self.factory() as session:
                min_score = get_float(session, "face_min_score", s.face_min_score)
                min_px = get_int(session, "face_min_px", s.face_min_px)
                rows = session.scalars(
                    select(File).where(
                        File.id.in_(set(file_ids)),
                        File.face_status == "pending",
                        File.is_trashed.is_(False),
                    )
                ).all()
                crops: list[tuple[Face, Image.Image, tuple[float, float, float, float]]] = []
                now = datetime.now(UTC).replace(tzinfo=None)
                for row in rows:
                    try:
                        img, scale = load_for_faces(
                            s.data_dir / row.stored_path, s.face_det_max_side
                        )
                        detected = self.engine.detect(img, min_score, min_px * scale)
                    except FaceModelUnavailableError:
                        raise
                    except Exception as e:
                        log.warning("face scan failed for file %s: %s", row.id, e)
                        row.face_status, row.face_error = "failed", f"could not scan: {e}"
                        continue
                    kept = 0
                    width, height = img.size
                    for d in detected:
                        vec = np.asarray(d.embedding, dtype=np.float32)
                        if not np.isfinite(vec).all() or np.linalg.norm(vec) == 0:
                            log.error("bad face embedding in file %s — dropped", row.id)
                            continue
                        x, y, w, h = d.box
                        face = Face(
                            file_id=row.id,
                            box_x=x / width,
                            box_y=y / height,
                            box_w=w / width,
                            box_h=h / height,
                            det_score=d.score,
                            embedding=to_blob(vec),
                            created_at=now,
                        )
                        session.add(face)
                        new.append((face, vec))
                        crops.append((face, img, d.box))
                        kept += 1
                    row.face_count, row.face_status, row.face_error = kept, "done", None
                session.flush()
                for face, img, box in crops:
                    try:
                        save_crop(img, box, s.faces_dir / f"{face.id}.jpg", s.face_crop_size)
                    except OSError:
                        log.exception("could not write crop for face %s", face.id)
                session.commit()
            if new:
                self._cluster(new)

    def _cluster(self, new: list[tuple[Face, np.ndarray]]) -> None:
        with self.clusterer.lock, self.factory() as session:
            live = set(
                session.scalars(
                    select(Face.id)
                    .join(File, File.id == Face.file_id)
                    .where(Face.id.in_([f.id for f, _ in new]), File.is_trashed.is_(False))
                )
            )
            for face, vec in new:
                if face.id in live:  # trashed meanwhile → stays out of the index
                    self.clusterer.index.add(face.id, vec)
            for face, _ in new:
                if face.id in live:
                    self.clusterer.assign(session, face.id)
            session.commit()
