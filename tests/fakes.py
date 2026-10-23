from dataclasses import dataclass

import numpy as np
from PIL import Image

from app.services.embedder import ModelUnavailableError
from app.services.face_engine import DetectedFace, FaceModelUnavailableError

# TestClient's default Host ("testserver") is refused by the app's Host check.
BASE_URL = "http://127.0.0.1"


class FakeEmbedder:
    """Deterministic stand-in for the model: a normalized 16x16 grayscale thumbnail.

    Resized copies of one picture embed close together; unrelated pictures don't.
    Images whose width is in `nan_widths` embed as NaN; `fail` makes every call raise."""

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim
        self.calls: list[int] = []  # batch sizes
        self.nan_widths: set[int] = set()
        self.fail = False
        self.unavailable = False

    def embed_images(self, images: list[Image.Image]) -> np.ndarray:
        self.calls.append(len(images))
        if self.unavailable:
            raise ModelUnavailableError("weights not downloaded")
        if self.fail:
            raise RuntimeError("model exploded")
        out = np.empty((len(images), self.dim), dtype=np.float32)
        for i, img in enumerate(images):
            px = np.asarray(img.convert("L").resize((16, 16)), dtype=np.float32).ravel()
            px -= px.mean()
            v = np.resize(px, self.dim)
            out[i] = v / (np.linalg.norm(v) or 1.0)
            if img.width in self.nan_widths:
                out[i] = np.nan
        return out

    def info(self) -> dict[str, str]:
        return {"model": "fake", "device": "cpu", "dtype": "float32", "loaded": "true"}


@dataclass
class FakeFace:
    """A scripted face, box in pixels of the upright image the worker passes in."""

    box: tuple[float, float, float, float]
    embedding: np.ndarray
    score: float = 0.9


class FakeFaceEngine:
    """Faces scripted per image size: `script[(w, h)] = [FakeFace, ...]`; others have none."""

    def __init__(self) -> None:
        self.script: dict[tuple[int, int], list[FakeFace]] = {}
        self.installed = True
        self.fail_sizes: set[tuple[int, int]] = set()
        self.calls = 0

    def available(self) -> bool:
        return self.installed

    def detect(self, image: Image.Image, min_score: float, min_side: float) -> list[DetectedFace]:
        if not self.installed:
            raise FaceModelUnavailableError("face model not installed")
        self.calls += 1
        if image.size in self.fail_sizes:
            raise RuntimeError("detector exploded")
        return [
            DetectedFace(f.box, f.score, np.zeros((5, 2), np.float32), f.embedding)
            for f in self.script.get(image.size, [])
            if f.score >= min_score and min(f.box[2], f.box[3]) >= min_side
        ]

    def info(self) -> dict[str, str]:
        return {"model": "fake", "installed": str(self.installed).lower(), "loaded": "true"}
