"""Build a small fake Google Takeout tree for screenshots and demos.

    uv run python scripts/make_demo_library.py --src SOME_PHOTOS --out DEMO_TAKEOUT

Use photos you have the rights to publish (CC0, for example from Wikimedia Commons, or your
own). Never feed it private photos if the screenshots will be shared.

For every JPG/PNG directly in --src it writes, under --out/Takeout/Google Photos/:
  Photos from 2021/<name>        the original
  Photos from 2021/<stem>-near   a smaller, re-encoded copy (a near-duplicate)
  Demo Album/<name>              an exact copy (Takeout stores album photos twice)
plus a `.supplemental-metadata.json` sidecar with `photoTakenTime.timestamp` per image.

It only reads --src and only writes --out. It refuses to run if --out is inside --src (or
--src inside --out) or if --out already contains anything, so it can never overwrite or
delete your files.
"""

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageOps

SOURCE_SUFFIXES = {".jpg", ".jpeg", ".png"}
YEAR_FOLDER = "Photos from 2021"
ALBUM_FOLDER = "Demo Album"
NEAR_SCALE = 0.75
NEAR_QUALITY = 70
FIRST_DAY = datetime(2021, 6, 1, 12, 0, tzinfo=UTC)
SECONDS_PER_DAY = 86400


def _write_sidecar(image: Path, timestamp: int) -> None:
    sidecar = image.with_name(image.name + ".supplemental-metadata.json")
    data = {"title": image.name, "photoTakenTime": {"timestamp": str(timestamp)}}
    sidecar.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _sources(src: Path) -> list[Path]:
    return sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() in SOURCE_SUFFIXES)


def build_demo_library(src: Path, out: Path) -> None:
    sources = _sources(src)
    root = out / "Takeout" / "Google Photos"
    year, album = root / YEAR_FOLDER, root / ALBUM_FOLDER
    year.mkdir(parents=True)
    album.mkdir(parents=True)
    base = int(FIRST_DAY.timestamp())
    for i, source in enumerate(sources):
        stamp = base + i * SECONDS_PER_DAY
        original = year / source.name
        shutil.copyfile(source, original)
        copy = album / source.name
        shutil.copyfile(source, copy)
        near = year / f"{source.stem}-near.jpg"
        with Image.open(source) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            size = (max(1, int(img.width * NEAR_SCALE)), max(1, int(img.height * NEAR_SCALE)))
            img.resize(size, Image.Resampling.LANCZOS).save(near, quality=NEAR_QUALITY)
        for image in (original, copy, near):
            _write_sidecar(image, stamp)


def _is_inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build a fake Takeout tree from sample photos.")
    parser.add_argument("--src", type=Path, required=True, help="folder of photos you may publish")
    parser.add_argument("--out", type=Path, required=True, help="new or empty output folder")
    args = parser.parse_args(argv)
    src, out = args.src.resolve(), args.out.resolve()
    if not src.is_dir():
        parser.error(f"--src is not a folder: {src}")
    if _is_inside(out, src) or _is_inside(src, out):
        parser.error("--out and --src must not contain each other")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        parser.error(f"--out must be a new or empty folder: {out}")
    if not _sources(src):
        parser.error(f"no JPG/PNG photos in --src: {src}")
    build_demo_library(src, out)
    print(f"Demo Takeout written to {out / 'Takeout'}")


if __name__ == "__main__":
    sys.exit(main())
