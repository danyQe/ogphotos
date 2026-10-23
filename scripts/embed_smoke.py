"""Smoke-test EmbeddingGemma 2 image embeddings before building on them.

Loads text + vision only, embeds two images (and a resized copy of the first) through
each candidate input form, and prints shapes, NaN checks, cosines and timing.

Usage:
    uv run python scripts/embed_smoke.py [IMAGE_A] [IMAGE_B]

Without arguments, two synthetic test images are generated.
"""

import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services import analysis  # noqa: E402  (registers the HEIC opener)
from app.services.embedder import select_device_dtype  # noqa: E402

MODEL = "google/embeddinggemma-2"
DIM = 512


def synthetic(seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 256, (6, 8, 3), dtype=np.uint8)).resize(
        (640, 480), Image.Resampling.BICUBIC
    )


def main() -> None:
    if len(sys.argv) == 3:
        a = analysis.load_for_analysis(Path(sys.argv[1]), 1024)
        b = analysis.load_for_analysis(Path(sys.argv[2]), 1024)
    else:
        a, b = synthetic(1), synthetic(2)
    a_small = a.resize((a.width // 3, a.height // 3))

    device, dtype = select_device_dtype("auto", torch)
    print(f"torch {torch.__version__}, device={device}, dtype={dtype}")
    t = time.perf_counter()
    model = SentenceTransformer(
        MODEL,
        device=device,
        config_kwargs={"audio_config": None},
        model_kwargs={"torch_dtype": dtype},
    )
    print(
        f"loaded in {time.perf_counter() - t:.1f}s; modalities: {getattr(model, 'modalities', '?')}"
    )

    forms = {
        "dict(image=PIL)": [{"image": img} for img in (a, a_small, b)],
        "PIL": [a, a_small, b],
    }
    for name, inputs in forms.items():
        try:
            t = time.perf_counter()
            emb = model.encode(
                inputs,
                batch_size=3,
                truncate_dim=DIM,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
            dt = (time.perf_counter() - t) / 3
        except Exception as e:  # noqa: BLE001
            print(f"[{name}] FAILED: {type(e).__name__}: {e}")
            continue
        emb = np.asarray(emb, dtype=np.float32)
        print(
            f"[{name}] shape={emb.shape} dtype={emb.dtype} finite={np.isfinite(emb).all()} "
            f"norms={np.linalg.norm(emb, axis=1).round(4)} {dt * 1000:.0f} ms/img"
        )
        print(f"    cos(a, a_resized)={emb[0] @ emb[1]:.4f}  cos(a, b)={emb[0] @ emb[2]:.4f}")

    text = model.encode(["a photo"], truncate_dim=DIM, normalize_embeddings=True)
    print(f"[text] shape={np.asarray(text).shape} (text path works)")


if __name__ == "__main__":
    main()
