"""Face detection + embedding behind an interface (tests inject a fake).

InsightFace buffalo_l: SCRFD-10G finds faces and 5 landmarks, ArcFace R50 embeds a
112×112 aligned crop. Run with onnxruntime, on a GPU provider when the installed build has
one (see select_providers). Weights are for non-commercial use."""

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
from PIL import Image

from app.config import Settings
from app.services.face_geometry import (
    align_face,
    anchor_centers,
    distance2bbox,
    distance2kps,
    nms,
)

log = logging.getLogger(__name__)

FACE_DIM = 512
DET_FILE = "det_10g.onnx"
REC_FILE = "w600k_r50.onnx"
INSTALL_HINT = "download it at /setup/models"
STRIDES = (8, 16, 32)
NMS_IOU = 0.4


@dataclass(frozen=True)
class DetectedFace:
    box: tuple[float, float, float, float]  # x, y, w, h in pixels of the image passed in
    score: float
    landmarks: np.ndarray  # (5, 2)
    embedding: np.ndarray  # (512,) float32, L2-normalized


class FaceModelUnavailableError(RuntimeError):
    pass


def select_providers(requested: str, available: list[str]) -> list[str]:
    """onnxruntime providers for `Settings.device`, CPU always last as the fallback.

    auto/cuda use CUDA (onnxruntime-gpu), auto also DirectML (onnxruntime-directml);
    whichever onnxruntime build is installed decides what is available. CoreML only
    when asked for with "mps": it recompiles the recognizer for every face count."""
    wanted = {
        "auto": ["CUDAExecutionProvider", "DmlExecutionProvider"],
        "cuda": ["CUDAExecutionProvider"],
        "mps": ["CoreMLExecutionProvider"],
    }.get(requested, [])
    gpu = [p for p in wanted if p in available][:1]
    return [*gpu, "CPUExecutionProvider"]


def _preload_cuda_libraries(ort: Any) -> None:
    """onnxruntime-gpu ships no CUDA/cuDNN; reuse the CUDA 13 libraries the cu130 torch
    wheels bring (loaded by importing torch, or found in the nvidia-* packages). On
    failure ORT drops the CUDA provider and the session runs on the CPU."""
    try:
        import torch  # noqa: F401

        ort.preload_dlls()
    except Exception as e:
        log.warning("could not preload CUDA libraries for onnxruntime: %s", e)


class FaceEngine(Protocol):
    @property
    def model_dir(self) -> Path:
        # Read live: first-run setup changes settings.data_dir after the engine is built.
        return self.settings.face_model_path

    def available(self) -> bool: ...

    def detect(self, image: Image.Image, min_score: float, min_side: float) -> list[DetectedFace]:
        """Faces scoring ≥ min_score whose short side ≥ min_side pixels (of `image`)."""
        ...

    def info(self) -> dict[str, str]: ...


class InsightFaceEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.det_size = settings.face_det_size
        self._lock = threading.Lock()
        self._det: Any = None
        self._rec: Any = None

    @property
    def model_dir(self) -> Path:
        # Read live: first-run setup changes settings.data_dir after the engine is built.
        return self.settings.face_model_path

    def available(self) -> bool:
        return all((self.model_dir / f).is_file() for f in (DET_FILE, REC_FILE))

    def info(self) -> dict[str, str]:
        return {
            "model": "InsightFace buffalo_l (SCRFD-10G + ArcFace R50)",
            "installed": str(self.available()).lower(),
            "loaded": str(self._det is not None).lower(),
            "providers": ", ".join(self._det.get_providers()) if self._det else "not loaded",
            "dir": str(self.model_dir),
        }

    def _load(self) -> tuple[Any, Any]:
        with self._lock:
            if self._det is None:
                if not self.available():
                    raise FaceModelUnavailableError(
                        f"face model not installed in {self.model_dir} — {INSTALL_HINT}"
                    )
                import onnxruntime as ort

                providers = select_providers(self.settings.device, ort.get_available_providers())
                if "CUDAExecutionProvider" in providers:
                    _preload_cuda_libraries(ort)
                options = ort.SessionOptions()
                if "DmlExecutionProvider" in providers:
                    # DirectML supports neither memory patterns nor parallel execution
                    options.enable_mem_pattern = False
                    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                self._det = ort.InferenceSession(
                    str(self.model_dir / DET_FILE), options, providers=providers
                )
                self._rec = ort.InferenceSession(
                    str(self.model_dir / REC_FILE), options, providers=providers
                )
                # ORT silently drops a GPU provider whose runtime libraries are missing
                log.info("face model on %s", ", ".join(self._det.get_providers()))
        return self._det, self._rec

    def detect(self, image: Image.Image, min_score: float, min_side: float) -> list[DetectedFace]:
        det, rec = self._load()
        rgb = np.asarray(image.convert("RGB"))
        h, w = rgb.shape[:2]
        size = self.det_size
        scale = size / max(h, w)
        nh, nw = max(1, round(h * scale)), max(1, round(w * scale))
        canvas = np.zeros((size, size, 3), np.uint8)
        canvas[:nh, :nw] = cv2.resize(rgb, (nw, nh))
        blob = ((canvas.astype(np.float32) - 127.5) / 128.0).transpose(2, 0, 1)[None]
        outs = det.run(None, {det.get_inputs()[0].name: blob})

        boxes, scores, kps = [], [], []
        for i, stride in enumerate(STRIDES):
            sc = outs[i].reshape(-1)
            keep = sc >= min_score
            if not keep.any():
                continue
            centers = anchor_centers(size // stride, size // stride, stride)[keep]
            boxes.append(distance2bbox(centers, outs[i + 3].reshape(-1, 4)[keep] * stride))
            kps.append(distance2kps(centers, outs[i + 6].reshape(-1, 10)[keep] * stride))
            scores.append(sc[keep])
        if not boxes:
            return []
        all_boxes = np.concatenate(boxes) / scale
        all_kps = np.concatenate(kps) / scale
        all_scores = np.concatenate(scores)

        picked: list[tuple[tuple[float, float, float, float], float, np.ndarray]] = []
        for i in nms(all_boxes, all_scores, NMS_IOU):
            x1, y1, x2, y2 = np.clip(all_boxes[i], 0, [w, h, w, h])
            if min(x2 - x1, y2 - y1) < min_side:
                continue
            picked.append(
                (
                    (float(x1), float(y1), float(x2 - x1), float(y2 - y1)),
                    float(all_scores[i]),
                    all_kps[i],
                )
            )
        if not picked:
            return []

        crops = np.stack([align_face(rgb, lm) for _, _, lm in picked]).astype(np.float32)
        vecs = rec.run(
            None, {rec.get_inputs()[0].name: ((crops - 127.5) / 127.5).transpose(0, 3, 1, 2)}
        )[0]
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = np.divide(vecs, norms, out=np.zeros_like(vecs), where=norms > 0)
        return [
            DetectedFace(box, score, lm.astype(np.float32), vec.astype(np.float32))
            for (box, score, lm), vec in zip(picked, vecs, strict=True)
        ]
