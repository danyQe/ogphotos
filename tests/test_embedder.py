from types import SimpleNamespace

import pytest

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
