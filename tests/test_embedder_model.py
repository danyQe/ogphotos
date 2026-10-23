"""Real EmbeddingGemma 2 checks. Slow; run with `uv run pytest -m model`."""

from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from app.config import Settings
from app.services.embedder import GemmaEmbedder
from tests.imgutil import photo_bytes

pytestmark = pytest.mark.model


@pytest.fixture(scope="module")
def embedder() -> GemmaEmbedder:
    return GemmaEmbedder(Settings())


def _img(seed: int, size: tuple[int, int]) -> Image.Image:
    return Image.open(BytesIO(photo_bytes(seed, size=size))).convert("RGB")


def test_real_embeddings_are_finite_normalized_and_sensible(embedder: GemmaEmbedder) -> None:
    vecs = embedder.embed_images([_img(1, (1024, 768)), _img(1, (320, 240)), _img(2, (1024, 768))])
    assert vecs.shape == (3, 512) and vecs.dtype == np.float32
    assert np.isfinite(vecs).all()
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-3)
    assert vecs[0] @ vecs[1] > vecs[0] @ vecs[2]
    info = embedder.info()
    assert info["loaded"] == "true" and info["dtype"] in ("float32", "bfloat16")
