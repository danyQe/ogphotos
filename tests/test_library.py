import pytest
from fastapi.testclient import TestClient

from app.routes import library as library_routes
from app.routes.pages import duration
from tests.imgutil import photo_bytes, video_bytes
from tests.test_groups_api import _upload


@pytest.fixture
def dated(client: TestClient) -> dict[str, int]:
    names = {
        "jan21.jpg": "2021:01:10 10:00:00",
        "mar21.jpg": "2021:03:05 10:00:00",
        "mar21b.jpg": "2021:03:20 10:00:00",
        "jun22.jpg": "2022:06:01 10:00:00",
    }
    ids = _upload(
        client, [(n, photo_bytes(i + 10, exif_date=d)) for i, (n, d) in enumerate(names.items())]
    )
    return dict(zip(names, ids, strict=True))


def _names(body: dict) -> list[str]:
    return [f["original_name"] for f in body["files"]]


def test_list_files_newest_first_with_filters(client: TestClient, dated: dict[str, int]) -> None:
    body = client.get("/api/files").json()
    assert body["total"] == 4
    assert _names(body) == ["jun22.jpg", "mar21b.jpg", "mar21.jpg", "jan21.jpg"]
    assert _names(client.get("/api/files", params={"year": 2021}).json()) == [
        "mar21b.jpg",
        "mar21.jpg",
        "jan21.jpg",
    ]
    march = client.get("/api/files", params={"year": 2021, "month": 3}).json()
    assert _names(march) == ["mar21b.jpg", "mar21.jpg"]
    page2 = client.get("/api/files", params={"per_page": 3, "page": 2}).json()
    assert _names(page2) == ["jan21.jpg"] and page2["total"] == 4

    client.post(f"/api/files/{dated['jan21.jpg']}/trash")
    assert client.get("/api/files").json()["total"] == 3
    assert _names(client.get("/api/files", params={"trashed": "true"}).json()) == ["jan21.jpg"]


def test_file_metadata(client: TestClient, dated: dict[str, int]) -> None:
    meta = client.get(f"/api/files/{dated['jun22.jpg']}").json()
    assert meta["original_name"] == "jun22.jpg" and meta["taken_at_source"] == "exif"
    assert meta["analysis_status"] == "done" and meta["is_trashed"] is False
    assert client.get("/api/files/999").status_code == 404


def test_library_page_filters_and_months(client: TestClient, dated: dict[str, int]) -> None:
    page = client.get("/library").text
    assert 'href="/library?year=2022"' in page and 'href="/library?year=2021"' in page
    assert "June 2022" in page and "March 2021" in page and "January 2021" in page
    assert page.count("March 2021") == 1  # one heading per month
    for fid in dated.values():
        assert f'href="/files/{fid}"' in page

    y2021 = client.get("/library", params={"year": 2021}).text
    assert 'href="/library?year=2021&month=3"' in y2021 and "June 2022" not in y2021
    march = client.get("/library", params={"year": 2021, "month": 3}).text
    assert "January 2021" not in march and "2 photos" in march


def test_library_infinite_scroll_chunks(
    client: TestClient, dated: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(library_routes, "PER_PAGE", 2)
    first = client.get("/library").text
    assert 'hx-get="/library/grid?page=2&after=2021-03"' in first
    second = client.get("/library/grid", params={"page": 2, "after": "2021-03"}).text
    assert "March 2021" not in second  # heading continues from the previous chunk
    assert "January 2021" in second and "hx-get" not in second  # last chunk


def test_library_empty(client: TestClient) -> None:
    assert "Library is empty" in client.get("/library").text


def test_file_page_shows_metadata_and_similar(client: TestClient) -> None:
    a, b, other = _upload(
        client,
        [
            ("a.jpg", photo_bytes(1, size=(800, 600), exif_date="2022:06:01 12:00:01")),
            ("b.jpg", photo_bytes(1, size=(640, 480), exif_date="2022:06:01 12:00:02")),
            ("c.jpg", photo_bytes(3)),
        ],
    )
    page = client.get(f"/files/{a}").text
    assert "a.jpg" in page and "800 × 600" in page and "Move to Trash" in page
    assert f"/api/files/{a}/original" in page  # JPEG shown inline
    similar = page.split("Similar photos", 1)[1]
    assert similar.index(f'href="/files/{b}"') < similar.index(f'href="/files/{other}"')
    assert "/groups/" in page

    client.post(f"/api/files/{a}/trash")
    trashed = client.get(f"/files/{a}").text
    assert "In Trash" in trashed and 'data-action="restore"' in trashed
    assert client.get("/files/999").status_code == 404


def test_duration_filter() -> None:
    assert duration(None) == "" and duration(42.4) == "0:42" and duration(3723) == "1:02:03"


def test_video_tile_has_thumbnail_and_badge(client: TestClient, tmp_path) -> None:
    [vid] = _upload(client, [("clip.mp4", video_bytes(tmp_path, seconds=2.0))])
    html = client.get("/library").text
    assert f'src="/api/files/{vid}/thumb"' in html and 'class="play-badge"' in html
    assert "0:02" in html
    assert client.get(f"/api/files/{vid}/thumb").status_code == 200
    page = client.get(f"/files/{vid}").text
    assert "<video" in page and "0:02" in page
