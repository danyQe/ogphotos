import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from make_demo_library import build_demo_library, main  # noqa: E402


def _make_sources(src: Path, count: int = 4) -> None:
    src.mkdir()
    for i in range(count):
        # Distinct gradients so each source is a different "photo".
        img = Image.linear_gradient("L").resize((320, 240)).convert("RGB")
        img = Image.blend(img, Image.new("RGB", img.size, (40 * i, 90, 255 - 50 * i)), 0.5)
        img.save(src / f"photo{i}.jpg", quality=92)


def test_demo_library_builds_takeout_tree(tmp_path: Path) -> None:
    src, out = tmp_path / "src", tmp_path / "out"
    _make_sources(src)
    before = sorted(p.name for p in src.iterdir())

    build_demo_library(src, out)

    year = out / "Takeout" / "Google Photos" / "Photos from 2021"
    album = out / "Takeout" / "Google Photos" / "Demo Album"
    year_images = sorted(p for p in year.iterdir() if p.suffix == ".jpg")
    album_images = sorted(p for p in album.iterdir() if p.suffix == ".jpg")
    # 4 originals + 4 resized near-duplicates in the year folder; 4 exact copies in the album
    assert len(year_images) == 8
    assert len(album_images) == 4
    for i in range(4):
        original = year / f"photo{i}.jpg"
        assert (album / f"photo{i}.jpg").read_bytes() == original.read_bytes()
        near = year / f"photo{i}-near.jpg"
        assert near.read_bytes() != original.read_bytes()
        with Image.open(near) as n, Image.open(original) as o:
            assert n.size[0] < o.size[0]

    sidecars = sorted(out.rglob("*.supplemental-metadata.json"))
    assert len(sidecars) == 12
    for sc in sidecars:
        data = json.loads(sc.read_text(encoding="utf-8"))
        assert int(data["photoTakenTime"]["timestamp"]) > 0
        assert (sc.parent / sc.name.removesuffix(".supplemental-metadata.json")).is_file()
    # the source folder is untouched
    assert sorted(p.name for p in src.iterdir()) == before


def test_refuses_out_inside_src(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _make_sources(src, 1)
    with pytest.raises(SystemExit):
        main(["--src", str(src), "--out", str(src / "demo")])
    assert not (src / "demo").exists()


def test_refuses_non_empty_out(tmp_path: Path) -> None:
    src, out = tmp_path / "src", tmp_path / "out"
    _make_sources(src, 1)
    out.mkdir()
    (out / "keep.txt").write_text("mine")
    with pytest.raises(SystemExit):
        main(["--src", str(src), "--out", str(out)])
    assert [p.name for p in out.iterdir()] == ["keep.txt"]


def test_refuses_src_inside_out(tmp_path: Path) -> None:
    out = tmp_path / "out"
    src = out / "src"
    out.mkdir()
    _make_sources(src, 1)
    with pytest.raises(SystemExit):
        main(["--src", str(src), "--out", str(out)])


def test_refuses_empty_src(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    with pytest.raises(SystemExit):
        main(["--src", str(src), "--out", str(tmp_path / "out")])
