import pytest
from PIL import Image, ImageDraw

from app.config import Settings
from app.services.face_engine import InsightFaceEngine
from app.services.locations import resolve_settings

pytestmark = pytest.mark.model


def test_real_engine_finds_no_face_in_shapes() -> None:
    settings: Settings = resolve_settings()[0]
    engine = InsightFaceEngine(settings)
    if not engine.available():
        pytest.skip("face model not installed")
    img = Image.new("RGB", (800, 600), (40, 90, 160))
    ImageDraw.Draw(img).ellipse((300, 200, 500, 400), fill=(230, 200, 60))
    assert engine.detect(img, 0.6, 20) == []
