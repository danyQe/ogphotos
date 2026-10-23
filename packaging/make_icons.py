"""Render the OGphotos app icon with Pillow: `python packaging/make_icons.py OUT_DIR`.

Writes ogphotos.png (512x512), ogphotos.ico (Windows) and ogphotos.icns (macOS): a rounded
square in the app's accent color with "OG" in the surface color, both read from
app/static/style.css. Icons are generated at build time; no binary image is committed."""

import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
STYLE_CSS = ROOT / "app" / "static" / "style.css"

CANVAS = 1024  # drawn large, then downscaled for smooth edges
PNG_SIZE = 512
CORNER = 0.22  # corner radius as a fraction of the side
FONT_SIZE = 0.48  # text height as a fraction of the side
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def css_color(var: str, css: Path = STYLE_CSS) -> str:
    """The first (light-theme) value of a CSS custom property, e.g. `--accent`."""
    match = re.search(rf"{re.escape(var)}\s*:\s*(#[0-9a-fA-F]{{6}})", css.read_text("utf-8"))
    if match is None:
        raise ValueError(f"{var} not found in {css}")
    return match.group(1).lower()


def render(size: int = CANVAS) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=int(size * CORNER), fill=css_color("--accent")
    )
    font = ImageFont.load_default(size=int(size * FONT_SIZE))
    text_color = css_color("--surface")
    draw.text(
        (size / 2, size / 2),
        "OG",
        font=font,
        fill=text_color,
        anchor="mm",
        stroke_width=max(1, size // 64),  # a bolder look from the regular default font
        stroke_fill=text_color,
    )
    return img


def make_icons(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    big = render()
    png, ico, icns = (out_dir / f"ogphotos.{ext}" for ext in ("png", "ico", "icns"))
    big.resize((PNG_SIZE, PNG_SIZE), Image.Resampling.LANCZOS).save(png)
    big.save(ico, sizes=ICO_SIZES)
    big.save(icns)  # Pillow writes the standard sizes (16–1024) as PNG entries
    return [png, ico, icns]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python packaging/make_icons.py OUT_DIR", file=sys.stderr)
        return 2
    for path in make_icons(Path(argv[0])):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
