"""Run the real face engine on photos and print faces, timings and cross-photo cosines.

Usage:
    uv run python scripts/face_smoke.py PHOTO [PHOTO ...]
"""

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services.face_engine import InsightFaceEngine  # noqa: E402
from app.services.face_worker import load_for_faces  # noqa: E402
from app.services.locations import resolve_settings  # noqa: E402


def main() -> None:
    settings = resolve_settings()[0]
    engine = InsightFaceEngine(settings)
    vecs: list[tuple[str, np.ndarray]] = []
    for arg in sys.argv[1:]:
        img, scale = load_for_faces(Path(arg), settings.face_det_max_side)
        t = time.perf_counter()
        faces = engine.detect(img, settings.face_min_score, settings.face_min_px * scale)
        print(f"{arg}: {len(faces)} faces in {time.perf_counter() - t:.2f}s ({img.size})")
        for i, f in enumerate(faces):
            print(f"  #{i} score={f.score:.2f} box={[round(v) for v in f.box]}")
            vecs.append((f"{Path(arg).name}#{i}", f.embedding))
    for i, (a, va) in enumerate(vecs):
        for b, vb in vecs[i + 1 :]:
            print(f"cos({a}, {b}) = {float(va @ vb):.3f}")


if __name__ == "__main__":
    main()
