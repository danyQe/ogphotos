from collections.abc import Iterator
from typing import Any

from fastapi import Request
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Base, Setting
from app.services.locations import migrate_legacy_db


def make_engine(settings: Settings) -> Engine:
    engine = create_engine(
        f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


# Columns added to `files` after libraries already existed (create_all only adds tables)
FILE_COLUMNS_ADDED = {
    "duration_s": "FLOAT",
    "face_status": "VARCHAR(10) NOT NULL DEFAULT 'pending'",
    "face_error": "TEXT",
    "face_count": "INTEGER NOT NULL DEFAULT 0",
}


def _migrate(engine: Engine) -> None:
    with engine.begin() as conn:
        have = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(files)")}
        added = [col for col in FILE_COLUMNS_ADDED if col not in have]
        for col in added:
            conn.exec_driver_sql(f"ALTER TABLE files ADD COLUMN {col} {FILE_COLUMNS_ADDED[col]}")
        if "face_status" in added:
            conn.exec_driver_sql(
                "UPDATE files SET face_status = 'skipped' WHERE media_type != 'image'"
            )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_files_face_status ON files (face_status)"
        )


def init_db(settings: Settings) -> tuple[Engine, sessionmaker[Session]]:
    """Create data dirs, tables, and seed tunable settings. Idempotent."""
    migrate_legacy_db(settings.data_dir)
    for d in (
        settings.library_dir,
        settings.thumbs_dir,
        settings.trash_dir,
        settings.tmp_dir,
        settings.faces_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)

    engine = make_engine(settings)
    Base.metadata.create_all(engine)
    _migrate(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    with factory() as s:
        existing = set(s.scalars(select(Setting.key)))
        for key, value in settings.tunable_defaults().items():
            if key not in existing:
                s.add(Setting(key=key, value=value))
        s.commit()

    return engine, factory


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as s:
        yield s
