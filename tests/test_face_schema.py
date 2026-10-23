from datetime import datetime

from sqlalchemy import text

from app.config import Settings
from app.db import init_db
from app.models import Face, File, Person


def _file(sha: str, media_type: str) -> File:
    return File(
        sha256=sha,
        original_name=f"{sha}.jpg",
        stored_path=f"library/2020/{sha}.jpg",
        media_type=media_type,
        mime="image/jpeg" if media_type == "image" else "video/mp4",
        size_bytes=1,
        uploaded_at=datetime(2020, 1, 1),
        analysis_status="done",
    )


def test_new_tables_and_defaults(settings: Settings) -> None:
    engine, factory = init_db(settings)
    with factory() as s:
        f = _file("a" * 64, "image")
        s.add(f)
        s.flush()
        p = Person(label_no=1, created_at=datetime(2020, 1, 1), updated_at=datetime(2020, 1, 1))
        s.add(p)
        s.flush()
        s.add(
            Face(
                file_id=f.id,
                box_x=0.1,
                box_y=0.1,
                box_w=0.2,
                box_h=0.2,
                det_score=0.9,
                embedding=b"\0" * 2048,
                person_id=p.id,
                assigned_by="auto",
                created_at=datetime(2020, 1, 1),
            )
        )
        s.commit()
        assert f.face_status == "pending" and f.face_count == 0
        assert p.face_count == 0 and p.hidden is False and p.name is None
    assert settings.faces_dir.is_dir()
    assert settings.face_model_path == settings.data_dir / "models" / "buffalo_l"
    engine.dispose()


def test_migrates_legacy_files_table(settings: Settings) -> None:
    """A library created before faces existed gets the new columns on startup."""
    engine, factory = init_db(settings)
    with factory() as s:
        s.add_all([_file("a" * 64, "image"), _file("b" * 64, "video")])
        s.commit()
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX ix_files_face_status")
        for col in ("face_status", "face_error", "face_count"):
            conn.exec_driver_sql(f"ALTER TABLE files DROP COLUMN {col}")
    engine.dispose()

    for _ in range(2):  # idempotent
        engine, factory = init_db(settings)
        with factory() as s:
            rows = dict(s.execute(text("SELECT media_type, face_status FROM files")).all())
            assert rows == {"image": "pending", "video": "skipped"}
            assert s.execute(text("SELECT face_count FROM files")).scalars().all() == [0, 0]
        engine.dispose()


def test_face_tunables_seeded(settings: Settings) -> None:
    engine, factory = init_db(settings)
    with factory() as s:
        keys = set(s.execute(text("SELECT key FROM settings")).scalars())
    assert {"face_min_score", "face_min_px", "face_match_cosine", "face_min_cluster"} <= keys
    engine.dispose()
