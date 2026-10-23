import cv2
import numpy as np
from PIL import Image


def sharpness(img: Image.Image, max_side: int) -> float:
    """Variance of the Laplacian on a grayscale copy downscaled to `max_side`.

    Downscaling first keeps scores comparable between a photo and its resized copies."""
    gray = img.convert("L")
    gray.thumbnail((max_side, max_side))
    return float(cv2.Laplacian(np.asarray(gray), cv2.CV_64F).var())
