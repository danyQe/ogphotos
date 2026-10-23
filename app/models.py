from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    phash: Mapped[str | None] = mapped_column(String(16))
    original_name: Mapped[str] = mapped_column(Text)
    stored_path: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(10))
    mime: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float | None] = mapped_column(Float)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    taken_at_source: Mapped[str | None] = mapped_column(String(10))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime)
    sharpness: Mapped[float | None] = mapped_column(Float)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    analysis_status: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    analysis_error: Mapped[str | None] = mapped_column(Text)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id"), index=True)
    is_trashed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime)
    face_status: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    face_error: Mapped[str | None] = mapped_column(Text)
    face_count: Mapped[int] = mapped_column(Integer, default=0)


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # use_alter breaks the files <-> groups FK cycle at CREATE time
    best_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("files.id", use_alter=True, name="fk_groups_best_file")
    )
    size: Mapped[int] = mapped_column(Integer, default=0)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class GroupExclusion(Base):
    """Pairs the user marked "not similar"; grouping must never re-join them."""

    __tablename__ = "group_exclusions"

    file_id_a: Mapped[int] = mapped_column(ForeignKey("files.id"), primary_key=True)
    file_id_b: Mapped[int] = mapped_column(ForeignKey("files.id"), primary_key=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label_no: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)  # "Person N"
    name: Mapped[str | None] = mapped_column(Text)
    # NULL = pick automatically; use_alter breaks the people <-> faces FK cycle
    cover_face_id: Mapped[int | None] = mapped_column(
        ForeignKey("faces.id", use_alter=True, name="fk_people_cover_face")
    )
    face_count: Mapped[int] = mapped_column(Integer, default=0)  # faces on non-trashed files
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class Face(Base):
    __tablename__ = "faces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), index=True, nullable=False)
    # Box as fractions (0-1) of the upright photo; files.width/height are pre-rotation
    box_x: Mapped[float] = mapped_column(Float)
    box_y: Mapped[float] = mapped_column(Float)
    box_w: Mapped[float] = mapped_column(Float)
    box_h: Mapped[float] = mapped_column(Float)
    det_score: Mapped[float] = mapped_column(Float)
    embedding: Mapped[bytes] = mapped_column(LargeBinary)  # float32, 512-d, L2-normalized
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"), index=True)
    assigned_by: Mapped[str | None] = mapped_column(String(4))  # auto | user
    created_at: Mapped[datetime] = mapped_column(DateTime)


class FaceExclusion(Base):
    """Faces the user marked "not this person"; they never join that person again."""

    __tablename__ = "face_exclusions"

    face_id: Mapped[int] = mapped_column(ForeignKey("faces.id"), primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), primary_key=True)
