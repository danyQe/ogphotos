from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy.orm import Session

from app.models import Face, File
from app.services.vector_index import to_blob

if TYPE_CHECKING:  # face_index arrives in Task 4
    from app.services.face_index import FaceIndex


def face_vec(person: int, photo: int, noise: float = 0.5, dim: int = 512) -> np.ndarray:
    """`person`'s face in one photo: a fixed direction plus per-photo jitter.
    Same person ≈ 0.8 cosine, different people ≈ 0."""
    base = np.random.default_rng(person).standard_normal(dim)
    base /= np.linalg.norm(base)
    jit = np.random.default_rng(10_000 + person * 1000 + photo).standard_normal(dim)
    v = base + noise * jit / np.linalg.norm(jit)
    return (v / np.linalg.norm(v)).astype(np.float32)


_n = 0


def make_file(session: Session, trashed: bool = False) -> File:
    global _n
    _n += 1
    f = File(
        sha256=f"{_n:064d}",
        original_name=f"p{_n}.jpg",
        stored_path=f"{'trash' if trashed else 'library'}/2020/p{_n}.jpg",
        media_type="image",
        mime="image/jpeg",
        size_bytes=1,
        uploaded_at=datetime(2020, 1, 1),
        taken_at=datetime(2020, 1, 1, 0, 0, _n % 60),
        analysis_status="done",
        face_status="done",
        is_trashed=trashed,
    )
    session.add(f)
    session.flush()
    return f


def add_face(
    session: Session,
    index: "FaceIndex",
    vec: np.ndarray,
    file: File | None = None,
    score: float = 0.9,
) -> Face:
    file = file or make_file(session)
    face = Face(
        file_id=file.id,
        box_x=0.1,
        box_y=0.1,
        box_w=0.3,
        box_h=0.3,
        det_score=score,
        embedding=to_blob(vec),
        created_at=datetime(2020, 1, 1),
    )
    session.add(face)
    session.flush()
    if not file.is_trashed:
        index.add(face.id, vec)
    return face
