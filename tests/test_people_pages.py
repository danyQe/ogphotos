from fastapi.testclient import TestClient

from tests.test_people_api import _two_people


def test_people_page_lists_people(client: TestClient) -> None:
    a, _ = _two_people(client)
    html = client.get("/people").text
    assert "Person 1" in html and "Person 2" in html and f'href="/people/{a}"' in html
    assert 'href="/people/unsorted"' in html


def test_people_page_shows_install_banner_without_model(client: TestClient) -> None:
    client.app.state.face_engine.installed = False
    html = client.get("/people").text
    assert 'href="/setup/models"' in html and "fetch_face_model" not in html


def test_person_page_views(client: TestClient) -> None:
    a, _ = _two_people(client)
    photos = client.get(f"/people/{a}").text
    assert 'class="person-name"' in photos and photos.count('class="tile"') == 3
    faces = client.get(f"/people/{a}?view=faces").text
    assert faces.count('class="face-pick"') == 3
    assert client.get("/people/999").status_code == 404
    assert client.get(f"/people/{a}/grid?page=2").status_code == 200


def test_unsorted_page_and_file_chips(client: TestClient) -> None:
    a, _ = _two_people(client)
    assert client.get("/people/unsorted").status_code == 200
    file_id = client.get(f"/api/people/{a}").json()["photos"][0]["file_id"]
    html = client.get(f"/files/{file_id}").text
    assert f'href="/people/{a}"' in html and 'data-box="' in html and 'class="facebox"' in html


def test_hidden_person_is_not_listed_or_chipped(client: TestClient) -> None:
    a, b = _two_people(client)
    assert client.patch(f"/api/people/{b}", json={"hidden": True}).status_code == 200
    assert f'href="/people/{b}"' not in client.get("/people").text
    assert f'href="/people/{b}"' in client.get("/people?hidden=true").text
    file_id = client.get(f"/api/people/{b}").json()["photos"][0]["file_id"]
    assert f'href="/people/{b}"' not in client.get(f"/files/{file_id}").text


def test_person_names_are_escaped(client: TestClient) -> None:
    a, _ = _two_people(client)
    evil = '"><script>alert(1)</script>'
    assert client.patch(f"/api/people/{a}", json={"name": evil}).status_code == 200
    for url in ("/people", f"/people/{a}"):
        html = client.get(url).text
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html


def test_people_status_element_comes_first(client: TestClient) -> None:
    _two_people(client)
    html = client.get("/people").text
    assert 'class="status"' in html
    assert html.index('class="status"') < html.index('class="person-name"')
