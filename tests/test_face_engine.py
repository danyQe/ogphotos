import numpy as np
import pytest
from PIL import Image

from app.config import Settings
from app.services.face_engine import (
    FaceModelUnavailableError,
    InsightFaceEngine,
    select_providers,
)
from tests.faceutil import face_vec
from tests.fakes import FakeFace, FakeFaceEngine


def test_engine_without_model_files(settings: Settings) -> None:
    engine = InsightFaceEngine(settings)
    assert engine.available() is False
    assert engine.info()["installed"] == "false"
    with pytest.raises(FaceModelUnavailableError, match="/setup/models"):
        engine.detect(Image.new("RGB", (64, 64)), 0.5, 10)


def test_face_vec_same_person_close_others_far() -> None:
    a1, a2, b1 = face_vec(1, 1), face_vec(1, 2), face_vec(2, 1)
    assert np.linalg.norm(a1) == pytest.approx(1, abs=1e-5)
    assert float(a1 @ a2) > 0.7 and abs(float(a1 @ b1)) < 0.2


def test_fake_engine_filters_like_the_real_one() -> None:
    eng = FakeFaceEngine()
    eng.script[(64, 48)] = [
        FakeFace((0, 0, 30, 30), face_vec(1, 1)),
        FakeFace((0, 0, 30, 30), face_vec(2, 1), score=0.3),
        FakeFace((0, 0, 10, 10), face_vec(3, 1)),
    ]
    faces = eng.detect(Image.new("RGB", (64, 48)), 0.5, 20)
    assert len(faces) == 1 and faces[0].score == 0.9
    assert eng.detect(Image.new("RGB", (10, 10)), 0.5, 20) == []


CPU = "CPUExecutionProvider"
CUDA = "CUDAExecutionProvider"
DML = "DmlExecutionProvider"
COREML = "CoreMLExecutionProvider"


@pytest.mark.parametrize(
    ("device", "available", "expected"),
    [
        ("auto", [CPU], [CPU]),
        ("auto", [CUDA, CPU], [CUDA, CPU]),
        ("auto", [DML, CPU], [DML, CPU]),
        ("auto", [CUDA, DML, CPU], [CUDA, CPU]),
        ("auto", [COREML, CPU], [CPU]),  # dynamic-batch recognizer: CoreML not by default
        ("cpu", [CUDA, CPU], [CPU]),
        ("cuda", [DML, CPU], [CPU]),
        ("mps", [COREML, CPU], [COREML, CPU]),
    ],
)
def test_select_providers(device, available, expected) -> None:
    assert select_providers(device, available) == expected
