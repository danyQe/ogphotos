"""Download the InsightFace buffalo_l face models once (the app itself stays offline).

Usage:
    uv run python scripts/fetch_face_model.py [--zip PATH_TO_buffalo_l.zip]

Verifies the zip and both model files against pinned sha256 values and installs
det_10g.onnx + w600k_r50.onnx into {library}/models/buffalo_l/.
Note: the InsightFace pretrained weights are licensed for non-commercial use only."""

import argparse
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.models_registry import FACE_MODEL_ID, get_spec  # noqa: E402
from app.services.locations import resolve_settings  # noqa: E402
from app.services.model_install import (  # noqa: E402
    InstallError,
    Progress,
    import_zip,
    install_model,
    is_installed,
    model_dir,
    record_install,
)


def _print_progress(progress: Progress, stop: threading.Event) -> None:
    while not stop.wait(0.5):
        if progress.total:
            pct = progress.done * 100 // progress.total
            print(f"\r  {pct}% ({progress.bytes_per_s / 1e6:.1f} MB/s)", end="", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", type=Path, help="use an already downloaded buffalo_l.zip")
    args = ap.parse_args()
    settings, needs_setup = resolve_settings()
    if needs_setup:
        print("choose a library folder first: start OGphotos and finish /setup")
        return 1
    spec = get_spec(FACE_MODEL_ID)
    target = model_dir(settings, spec)
    if is_installed(settings, spec):
        print(f"already installed in {target}")
        return 0
    try:
        if args.zip:
            import_zip(settings, spec, args.zip)
        else:
            print(f"downloading {spec.archive.url if spec.archive else spec.name}")
            progress, stop = Progress(), threading.Event()
            ticker = threading.Thread(target=_print_progress, args=(progress, stop), daemon=True)
            ticker.start()
            try:
                install_model(settings, spec, progress, threading.Event())
            finally:
                stop.set()
                print()
    except InstallError as e:
        print(e)
        return 1
    record_install(settings, spec, datetime.now(UTC))
    print(f"installed in {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
