import numpy as np

from app.services.face_geometry import (
    ARCFACE_TEMPLATE,
    align_face,
    anchor_centers,
    distance2bbox,
    distance2kps,
    nms,
    similarity_transform,
)


def _apply(m: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return pts @ m[:, :2].T + m[:, 2]


def test_similarity_transform_recovers_rotation_scale_shift() -> None:
    a = np.deg2rad(30)
    r = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    src = ARCFACE_TEMPLATE @ (2.0 * r).T + [10, 5]
    m = similarity_transform(src, ARCFACE_TEMPLATE)
    assert np.allclose(_apply(m, src), ARCFACE_TEMPLATE, atol=1e-3)


def test_similarity_transform_never_mirrors() -> None:
    mirrored = ARCFACE_TEMPLATE * [-1, 1]
    m = similarity_transform(mirrored, ARCFACE_TEMPLATE)
    assert np.linalg.det(m[:, :2]) > 0


def test_align_face_moves_landmark_onto_template() -> None:
    img = np.zeros((400, 400, 3), np.uint8)
    lm = ARCFACE_TEMPLATE * 3 + [50, 60]  # face 3× bigger, shifted
    x, y = lm[0].round().astype(int)
    img[y - 4 : y + 5, x - 4 : x + 5] = 255  # bright dot on the left eye
    out = align_face(img, lm)
    tx, ty = ARCFACE_TEMPLATE[0].round().astype(int)
    assert out.shape == (112, 112, 3)
    assert out[ty, tx].min() > 200 and out[100, 10].max() == 0


def test_anchor_centers_order_matches_scrfd() -> None:
    c = anchor_centers(2, 2, 8)
    assert c.tolist() == [[0, 0], [0, 0], [8, 0], [8, 0], [0, 8], [0, 8], [8, 8], [8, 8]]


def test_distance_decoding() -> None:
    pts = np.array([[100.0, 50.0]])
    assert distance2bbox(pts, np.array([[10.0, 5, 20, 15]])).tolist() == [[90, 45, 120, 65]]
    k = distance2kps(pts, np.arange(10, dtype=float)[None])
    assert k.shape == (1, 5, 2) and k[0, 1].tolist() == [102, 53]


def test_nms_drops_overlapping_lower_scores() -> None:
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=float)
    assert nms(boxes, np.array([0.9, 0.8, 0.7]), 0.4) == [0, 2]
    assert nms(np.empty((0, 4)), np.empty(0), 0.4) == []
