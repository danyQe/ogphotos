from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

JPEG_QUALITY = 85


def make_thumbnail(src: Path, dest: Path, size: int) -> None:
    """Write a JPEG thumbnail (long edge ≤ size) with EXIF orientation applied."""
    with Image.open(src) as original:
        img = ImageOps.exif_transpose(original)
        img.thumbnail((size, size))
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.getchannel("A"))
            img = flat
        else:
            img = img.convert("RGB")
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, "JPEG", quality=JPEG_QUALITY)


def _grab_frame(src: Path, at_s: float) -> np.ndarray | None:
    """One BGR frame ~`at_s` in (the first frame for short clips). OpenCV's FFMPEG
    backend applies the video's rotation flag itself."""
    cap = cv2.VideoCapture(str(src))
    try:
        if not cap.isOpened():
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        if fps > 0 and frames / fps > at_s * 2:  # skip the often-black first frame
            cap.set(cv2.CAP_PROP_POS_MSEC, at_s * 1000)
        ok, frame = cap.read()
        if not ok:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()


def make_video_thumbnail(src: Path, dest: Path, size: int, at_s: float = 1.0) -> bool:
    """JPEG poster frame (long edge ≤ size). False if no frame could be read."""
    frame = _grab_frame(src, at_s)
    if frame is None:
        return False
    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    img.thumbnail((size, size))
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=JPEG_QUALITY)
    return True
