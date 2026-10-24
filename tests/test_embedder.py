from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from app.config import Settings
from app.services.embedder import GemmaEmbedder, select_device_dtype


def _torch(cuda: bool = False, bf16: bool = False, mps: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        float16="f16",
        bfloat16="bf16",
        float32="f32",
        cuda=SimpleNamespace(is_available=lambda: cuda, is_bf16_supported=lambda: bf16),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
    )


@pytest.mark.parametrize(
    ("requested", "torch", "expected"),
    [
        ("auto", _torch(cuda=True, bf16=True), ("cuda", "bf16")),
        ("auto", _torch(cuda=True, bf16=False), ("cuda", "f32")),  # not f16
        ("auto", _torch(mps=True), ("mps", "f32")),
        ("auto", _torch(), ("cpu", "f32")),
        ("cpu", _torch(cuda=True, bf16=True), ("cpu", "f32")),
        ("cuda", _torch(), ("cpu", "f32")),
    ],
)
def test_select_device_dtype(requested, torch, expected) -> None:
    assert select_device_dtype(requested, torch) == expected


def test_gemma_embedder_is_lazy() -> None:
    e = GemmaEmbedder(Settings())
    assert e.dim == 512
    assert e.info()["loaded"] == "false"


class _OomModel:
    """encode() raises a CUDA-style OOM for batches larger than `fits`."""

    def __init__(self, fits: int) -> None:
        self.fits = fits
        self.calls: list[int] = []

    def encode(self, items, **kwargs):
        self.calls.append(len(items))
        if len(items) > self.fits:
            raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
        return np.ones((len(items), 4), dtype=np.float32)


def test_embed_splits_batch_on_out_of_memory_and_remembers() -> None:
    e = GemmaEmbedder(Settings())
    e._model = _OomModel(fits=2)
    imgs = [Image.new("RGB", (8, 8))] * 5
    assert e.embed_images(imgs).shape == (5, 4)
    e._model.calls.clear()
    e.embed_images(imgs)
    assert e._model.calls == [2, 2, 1]  # cap learned, no OOM retries


def test_embed_reraises_other_errors_and_single_image_oom() -> None:
    e = GemmaEmbedder(Settings())
    e._model = _OomModel(fits=0)
    with pytest.raises(RuntimeError, match="out of memory"):
        e.embed_images([Image.new("RGB", (8, 8))] * 3)
