from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import init_db
from app.main import create_app
from tests.fakes import BASE_URL, FakeEmbedder, FakeFaceEngine


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, FakeEmbedder(settings.embed_dim), face_engine=FakeFaceEngine())
    with TestClient(app, base_url=BASE_URL) as c:
        yield c


@pytest.fixture
def db(settings: Settings) -> Iterator[sessionmaker[Session]]:
    engine, factory = init_db(settings)
    yield factory
    engine.dispose()
