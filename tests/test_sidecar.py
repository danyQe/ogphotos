import json
from datetime import datetime

import pytest

from app.services.sidecar import find_sidecar, parse_sidecar

D = "Takeout/Photos from 2021"


def _sc(*names: str) -> dict[str, bytes]:
    return {f"{D}/{n}": n.encode() for n in names}


@pytest.mark.parametrize(
    "json_name",
    [
        "IMG_1.jpg.json",
        "IMG_1.jpg.supplemental-metadata.json",
        "IMG_1.jpg.supplemental-metad.json",  # Takeout truncation
    ],
)
def test_matches_plain_media(json_name: str) -> None:
    assert find_sidecar(f"{D}/IMG_1.jpg", _sc(json_name, "OTHER.jpg.json")) == json_name.encode()


def test_exact_json_preferred_over_supplemental() -> None:
    sc = _sc("IMG_1.jpg.supplemental-metadata.json", "IMG_1.jpg.json")
    assert find_sidecar(f"{D}/IMG_1.jpg", sc) == b"IMG_1.jpg.json"


@pytest.mark.parametrize(
    "json_name", ["IMG_1.jpg(1).json", "IMG_1.jpg.supplemental-metadata(1).json"]
)
def test_matches_numbered_duplicate(json_name: str) -> None:
    sc = _sc("IMG_1.jpg.json", json_name)
    assert find_sidecar(f"{D}/IMG_1(1).jpg", sc) == json_name.encode()


def test_numbered_json_not_matched_to_unnumbered_media() -> None:
    sc = _sc("IMG_1.jpg.supplemental-metadata(1).json")
    assert find_sidecar(f"{D}/IMG_1.jpg", sc) is None


def test_prefix_needs_dot_boundary() -> None:
    assert find_sidecar(f"{D}/IMG_1.jpg", _sc("IMG_1.jpg2.json")) is None


def test_other_directory_ignored_and_backslashes_ok() -> None:
    sc = {"Takeout/Album/IMG_1.jpg.json": b"album"}
    assert find_sidecar(f"{D}/IMG_1.jpg", sc) is None
    assert find_sidecar("Takeout\\Album\\IMG_1.jpg", sc) == b"album"


def test_bare_names_match_each_other() -> None:
    assert find_sidecar("IMG_1.jpg", {"IMG_1.jpg.json": b"x"}) == b"x"


def test_edited_copy_uses_the_original_sidecar() -> None:
    sc = _sc("IMG_1.jpg.supplemental-metadata.json", "IMG_2.jpg.json")
    assert find_sidecar(f"{D}/IMG_1-edited.jpg", sc) == b"IMG_1.jpg.supplemental-metadata.json"
    assert find_sidecar(f"{D}/IMG_3-edited.jpg", sc) is None
    own = _sc("IMG_1.jpg.json", "IMG_1-edited.jpg.json")
    assert find_sidecar(f"{D}/IMG_1-edited.jpg", own) == b"IMG_1-edited.jpg.json"


def test_parse_sidecar() -> None:
    data = json.dumps({"photoTakenTime": {"timestamp": "1609459200"}}).encode()
    assert parse_sidecar(data) == datetime(2021, 1, 1, 0, 0, 0)


@pytest.mark.parametrize(
    "data",
    [
        b"not json",
        b"{}",
        b'{"photoTakenTime": {"timestamp": "abc"}}',
        b'{"photoTakenTime": {"timestamp": "0"}}',
        b"[1, 2]",
        b"\xff\xfe",
    ],
)
def test_parse_sidecar_bad_input(data: bytes) -> None:
    assert parse_sidecar(data) is None
