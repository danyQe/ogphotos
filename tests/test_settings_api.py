import pytest
from fastapi.testclient import TestClient

from tests.test_groups_api import _burst

FACE_DEFAULTS = {
    "face_min_score": 0.6,
    "face_min_px": 60,
    "face_match_cosine": 0.45,
    "face_min_cluster": 3,
}


def test_get_settings(client: TestClient) -> None:
    body = client.get("/api/settings").json()
    assert body["thresholds"] == {
        "phash_max_distance": 8,
        "embed_min_cosine": 0.92,
        **FACE_DEFAULTS,
    }
    assert body["model"]["model"] == "fake"
    assert body["library"] == {"files": 0, "trashed": 0, "groups": 0}
    assert body["regroup"]["status"] == "idle"
    assert set(body["analysis"]) == {"pending", "done", "failed"}


def test_update_thresholds(client: TestClient) -> None:
    r = client.put("/api/settings", json={"phash_max_distance": 6, "embed_min_cosine": 0.95})
    assert r.status_code == 200
    assert r.json()["thresholds"] == {
        "phash_max_distance": 6,
        "embed_min_cosine": 0.95,
        **FACE_DEFAULTS,
    }
    assert client.get("/api/settings").json()["thresholds"]["phash_max_distance"] == 6


@pytest.mark.parametrize(
    "values",
    [
        {"phash_max_distance": 99},
        {"phash_max_distance": 4.5},
        {"embed_min_cosine": 0.1},
        {"embed_min_cosine": "high"},
        {"embed_dim": 128},
        {"phash_max_distance": 4, "embed_min_cosine": 2},  # all-or-nothing
    ],
)
def test_bad_thresholds_are_rejected(client: TestClient, values: dict) -> None:
    assert client.put("/api/settings", json=values).status_code == 400
    assert client.get("/api/settings").json()["thresholds"] == {
        "phash_max_distance": 8,
        "embed_min_cosine": 0.92,
        **FACE_DEFAULTS,
    }


def test_regroup_needs_confirm_and_keeps_exclusions(client: TestClient) -> None:
    burst, _ = _burst(client)
    [g] = client.get("/api/groups").json()["groups"]
    out = next(i for i in burst if i != g["best_file_id"])
    client.post(f"/api/groups/{g['id']}/remove/{out}")
    client.post(f"/api/groups/{g['id']}/reviewed")

    assert client.post("/api/regroup").status_code == 400
    r = client.post("/api/regroup", params={"confirm": "true"})
    assert r.status_code == 200
    client.app.state.regrouper.wait(30)
    state = client.get("/api/settings").json()["regroup"]
    assert state["status"] == "done" and state["groups"] == 1

    [new] = client.get("/api/groups").json()["groups"]
    assert new["reviewed"] is False  # reviewed marks are cleared
    assert set(new["preview_file_ids"]) == set(burst) - {out}  # "Not similar" survives


def test_regroup_applies_new_thresholds(client: TestClient) -> None:
    _burst(client)
    assert client.get("/api/groups").json()["total"] == 1
    client.put("/api/settings", json={"phash_max_distance": 0, "embed_min_cosine": 0.999})
    client.post("/api/regroup", params={"confirm": "true"})
    client.app.state.regrouper.wait(30)
    loose = client.get("/api/groups").json()["total"]
    client.put(
        "/api/settings", json={"phash_max_distance": 8, "embed_min_cosine": 0.92, **FACE_DEFAULTS}
    )
    client.post("/api/regroup", params={"confirm": "true"})
    client.app.state.regrouper.wait(30)
    assert loose <= 1 and client.get("/api/groups").json()["total"] == 1


def test_settings_page(client: TestClient) -> None:
    page = client.get("/settings").text
    assert 'name="phash_max_distance" value="8"' in page
    assert 'name="embed_min_cosine" value="0.92"' in page
    assert "Regroup all" in page and "reviewed marks are cleared" in page
    assert "fake" in page and "/static/settings.js" in page


def test_settings_include_faces(client: TestClient) -> None:
    body = client.get("/api/settings").json()
    faces = body["faces"]
    assert faces["model_installed"] is True and faces["enabled"] is True
    assert faces["recluster"]["status"] == "idle"
    assert faces["setup_url"] == "/setup/models"
    html = client.get("/settings").text
    assert 'name="face_match_cosine"' in html and 'data-action="recluster"' in html
    r = client.put("/api/settings", json={"face_match_cosine": 0.5})
    assert r.json()["thresholds"]["face_match_cosine"] == 0.5


def test_settings_page_has_about_and_disclaimer(client: TestClient) -> None:
    import app as app_pkg

    page = client.get("/settings").text
    assert "Not affiliated with Google. Google Photos is a trademark of Google LLC." in page
    assert f"OGphotos {app_pkg.__version__}" in page
    assert "MIT" in page and "THIRD_PARTY_LICENSES.md" in page
