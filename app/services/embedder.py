import logging
import threading
from typing import Any, Protocol

import numpy as np
from PIL import Image

from app.config import Settings

log = logging.getLogger(__name__)


class ModelUnavailableError(RuntimeError):
    """The model could not be loaded (e.g. weights not downloaded yet). Not the file's fault."""


class Embedder(Protocol):
    dim: int

    def embed_images(self, images: list[Image.Image]) -> np.ndarray:
        """(len(images), dim) float32, L2-normalized."""
        ...

    def info(self) -> dict[str, str]: ...


def select_device_dtype(requested: str, torch: Any) -> tuple[str, Any]:
    """CUDA (bf16 if supported) → MPS → CPU, float32 otherwise. Never float16: the
    model's activations overflow it and it returns NaN/degraded embeddings silently."""
    if requested in ("auto", "cuda") and torch.cuda.is_available():
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float32
        device = "cuda"
    elif requested in ("auto", "mps") and torch.backends.mps.is_available():
        device, dtype = "mps", torch.float32  # bf16 on MPS is unreliable
    else:
        device, dtype = "cpu", torch.float32
    if dtype == torch.float16:
        raise RuntimeError("refusing to run EmbeddingGemma 2 in float16")
    return device, dtype


class GemmaEmbedder:
    """EmbeddingGemma 2, text + vision encoders only, loaded on first use."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.dim = settings.embed_dim
        self._model: Any = None
        self._device = ""
        self._dtype = ""
        self._max_chunk = 0  # 0 = no cap; set after an out-of-memory
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> Any:
        with self._lock:
            if self._model is None:
                import torch
                from sentence_transformers import SentenceTransformer

                device, dtype = select_device_dtype(self.settings.device, torch)
                source = self.settings.embed_model_source
                log.info("loading %s on %s (%s)", source, device, dtype)
                try:
                    model = SentenceTransformer(
                        source,
                        device=device,
                        local_files_only=self.settings.embed_local_files_only,
                        config_kwargs={"audio_config": None},
                        model_kwargs={"torch_dtype": dtype},
                    )
                except Exception as e:
                    raise ModelUnavailableError(
                        f"could not load {source}: {e}. Download the model at /setup/models."
                    ) from e
                # Verified with scripts/embed_smoke.py: the image processor's soft-token budget
                model[
                    0
                ].processor.image_processor.max_soft_tokens = self.settings.embed_image_tokens
                self._model = model
                self._device, self._dtype = device, str(dtype).removeprefix("torch.")
            return self._model

    def embed_images(self, images: list[Image.Image]) -> np.ndarray:
        """Embed in chunks; on GPU out-of-memory, halve the chunk and keep the smaller cap."""
        model = self._ensure_loaded()
        out: list[np.ndarray] = []
        start = 0
        while start < len(images):
            chunk = images[start : start + (self._max_chunk or len(images))]
            try:
                out.append(self._encode(model, chunk))
            except Exception as e:
                if len(chunk) == 1 or "out of memory" not in str(e).lower():
                    raise
                self._max_chunk = len(chunk) // 2
                log.warning(
                    "out of memory at batch %d; retrying at %d", len(chunk), self._max_chunk
                )
                self._empty_cache()
                continue
            start += len(chunk)
        return np.concatenate(out) if out else np.empty((0, self.dim), dtype=np.float32)

    def _encode(self, model: Any, images: list[Image.Image]) -> np.ndarray:
        # Images take no task prefix. {"image": PIL} and bare PIL embed identically
        # (scripts/embed_smoke.py); the dict form is the documented multimodal input.
        vecs = model.encode(
            [{"image": img} for img in images],
            batch_size=len(images),
            truncate_dim=self.dim,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return np.asarray(vecs, dtype=np.float32)

    def _empty_cache(self) -> None:
        if self._device in ("cuda", "mps"):
            import torch

            getattr(torch, self._device).empty_cache()

    def info(self) -> dict[str, str]:
        return {
            "model": self.settings.embed_model,
            "device": self._device or "not loaded",
            "dtype": self._dtype or "not loaded",
            "loaded": str(self._model is not None).lower(),
            "image_tokens": str(self.settings.embed_image_tokens),
        }
