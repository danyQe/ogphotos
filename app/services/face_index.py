from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Face, File
from app.services.face_engine import FACE_DIM
from app.services.vector_index import VectorIndex


class FaceIndex(VectorIndex):
    """Embeddings of faces on non-trashed files, keyed by face id."""

    @classmethod
    def load(cls, session: Session, dim: int = FACE_DIM) -> "FaceIndex":
        rows = session.execute(
            select(Face.id, Face.embedding)
            .join(File, File.id == Face.file_id)
            .where(File.is_trashed.is_(False))
        ).all()
        return cls.from_rows([(r.id, r.embedding) for r in rows], dim)  # type: ignore[return-value]
