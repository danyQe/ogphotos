from io import BytesIO

from PIL import Image, ImageFilter

from app.services.sharpness import sharpness
from tests.imgutil import photo_bytes


def test_blurred_copy_scores_lower() -> None:
    img = Image.effect_noise((600, 400), 80).convert("RGB")
    blurry = img.filter(ImageFilter.GaussianBlur(4))
    assert sharpness(img, 1024) > 5 * sharpness(blurry, 1024)


def test_scale_independent_for_big_images() -> None:
    big = Image.open(BytesIO(photo_bytes(1, size=(1600, 1200))))
    huge = Image.open(BytesIO(photo_bytes(1, size=(3200, 2400))))
    a, b = sharpness(big, 800), sharpness(huge, 800)
    assert abs(a - b) / max(a, b) < 0.5
