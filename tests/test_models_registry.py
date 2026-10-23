import re
from pathlib import Path

import pytest

from app.models_registry import EMBED_MODEL_ID, FACE_MODEL_ID, REGISTRY, get_spec


def test_registry_ids_and_required_flags() -> None:
    assert set(REGISTRY) == {EMBED_MODEL_ID, FACE_MODEL_ID}
    assert get_spec(EMBED_MODEL_ID).required is True
    assert get_spec(FACE_MODEL_ID).required is False


def test_get_spec_unknown_id_raises() -> None:
    with pytest.raises(KeyError):
        get_spec("nope")


def test_face_model_uses_insightface_zip() -> None:
    spec = get_spec(FACE_MODEL_ID)
    assert spec.archive is not None
    assert (
        spec.archive.url
        == "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
    )
    assert spec.archive.sha256 == "80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f"
    assert {f.path: f.sha256 for f in spec.files} == {
        "det_10g.onnx": "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
        "w600k_r50.onnx": "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
    }
    assert "non-commercial" in spec.license_summary.lower()
    assert spec.total_size == spec.archive.size


def test_all_hashes_are_sha256_hex_and_paths_relative() -> None:
    for spec in REGISTRY.values():
        for f in spec.files + ((spec.archive,) if spec.archive else ()):
            assert re.fullmatch(r"[0-9a-f]{64}", f.sha256)
            assert not Path(f.path).is_absolute() and ".." not in Path(f.path).parts
            assert f.url.startswith("https://") and f.size > 0


def test_embed_model_has_config_and_weights() -> None:
    spec = get_spec(EMBED_MODEL_ID)
    paths = {f.path for f in spec.files}
    assert "config.json" in paths and "modules.json" in paths
    assert any(p.endswith(".safetensors") for p in paths)
    assert spec.archive is None
    assert spec.total_size == sum(f.size for f in spec.files)


def test_embed_model_pinned_apache_license() -> None:
    spec = get_spec(EMBED_MODEL_ID)
    assert spec.license_name == "Apache-2.0"
    assert spec.license_url == "https://www.apache.org/licenses/LICENSE-2.0"
    revision = "914f7f89142e33e77833254d9c9b90c3cef7303b"
    assert all(f"/resolve/{revision}/" in f.url for f in spec.files)
