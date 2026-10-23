"""Geometry for InsightFace models: SCRFD output decoding and ArcFace alignment."""

import cv2
import numpy as np

# Where ArcFace expects left eye, right eye, nose tip, left and right mouth corner (112×112)
ARCFACE_TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


def similarity_transform(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """2×3 rotation + uniform scale + shift mapping `src` points onto `dst` (Umeyama)."""
    src = np.asarray(src, np.float64)
    dst = np.asarray(dst, np.float64)
    mu_s, mu_d = src.mean(0), dst.mean(0)
    s_c, d_c = src - mu_s, dst - mu_d
    u, sig, vt = np.linalg.svd(d_c.T @ s_c / len(src))
    d = np.ones(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        d[1] = -1  # no reflections
    r = u @ np.diag(d) @ vt
    scale = (sig * d).sum() / ((s_c**2).sum() / len(src))
    t = mu_d - scale * r @ mu_s
    return np.hstack([scale * r, t[:, None]]).astype(np.float32)


def align_face(rgb: np.ndarray, landmarks: np.ndarray, size: int = 112) -> np.ndarray:
    m = similarity_transform(landmarks, ARCFACE_TEMPLATE * (size / 112))
    return cv2.warpAffine(rgb, m, (size, size), borderValue=0)


def anchor_centers(height: int, width: int, stride: int, num_anchors: int = 2) -> np.ndarray:
    """(x, y) of every anchor, row-major, each repeated `num_anchors` times (SCRFD order)."""
    ys, xs = np.mgrid[:height, :width]
    centers = (np.stack([xs, ys], axis=-1).reshape(-1, 2) * stride).astype(np.float32)
    return np.repeat(centers, num_anchors, axis=0)


def distance2bbox(points: np.ndarray, d: np.ndarray) -> np.ndarray:
    return np.stack(
        [
            points[:, 0] - d[:, 0],
            points[:, 1] - d[:, 1],
            points[:, 0] + d[:, 2],
            points[:, 1] + d[:, 3],
        ],
        axis=-1,
    )


def distance2kps(points: np.ndarray, d: np.ndarray) -> np.ndarray:
    return d.reshape(len(d), -1, 2) + points[:, None, :]


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    """Indices kept, best score first."""
    order = list(np.argsort(-scores))
    keep: list[int] = []
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while order:
        i = order.pop(0)
        keep.append(int(i))
        if not order:
            break
        rest = np.array(order)
        w = np.clip(
            np.minimum(boxes[i, 2], boxes[rest, 2]) - np.maximum(boxes[i, 0], boxes[rest, 0]),
            0,
            None,
        )
        h = np.clip(
            np.minimum(boxes[i, 3], boxes[rest, 3]) - np.maximum(boxes[i, 1], boxes[rest, 1]),
            0,
            None,
        )
        inter = w * h
        overlap = inter / (area[i] + area[rest] - inter)
        order = [int(j) for j, o in zip(rest, overlap, strict=True) if o <= iou]
    return keep
