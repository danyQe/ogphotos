import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.models import File

NAV = ["Upload", "Import", "Groups", "Library", "People", "Trash", "Settings"]


@pytest.mark.parametrize(
    "path", ["/upload", "/import", "/groups", "/library", "/trash", "/settings"]
)
def test_pages_render_with_nav(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    for label in NAV:
        assert label in r.text


def test_root_redirects_to_upload_when_empty(client: TestClient) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/upload"


def test_root_redirects_to_groups_when_library_has_files(client: TestClient) -> None:
    with client.app.state.session_factory() as s:
        s.add(
            File(
                sha256="0" * 64,
                original_name="a.jpg",
                stored_path="library/2021/a.jpg",
                media_type="image",
                mime="image/jpeg",
                size_bytes=1,
                uploaded_at=datetime.now(UTC),
                analysis_status="pending",
            )
        )
        s.commit()
    r = client.get("/", follow_redirects=False)
    assert r.headers["location"] == "/groups"


def test_no_external_assets(client: TestClient) -> None:
    html = client.get("/upload").text
    assert not re.search(r'(src|href)="(https?:)?//', html)
    assert client.get("/static/htmx.min.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_upload_page_has_uploader(client: TestClient) -> None:
    html = client.get("/upload").text
    assert 'data-batch-size="20"' in html
    assert f'data-batch-max-bytes="{200 * 1024 * 1024}"' in html
    assert "webkitdirectory" in html
    assert 'src="/static/upload.js"' in html
    assert client.get("/static/upload.js").status_code == 200


def test_upload_page_has_retry_button(client: TestClient) -> None:
    assert 'id="retry-failed"' in client.get("/upload").text
