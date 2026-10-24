# Contributing to OGphotos

Thanks for helping. Please read the [Code of Conduct](CODE_OF_CONDUCT.md) first.

## Setup

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                  # install dependencies (CPU torch/onnxruntime)
uv sync --no-default-groups --group dev --group cuda   # NVIDIA GPU instead; then `uv run --no-sync`uv run python -m app.launcher            # run the app
uv run uvicorn app.main:create_app --factory --reload   # or the dev server at http://127.0.0.1:8000
```

Use `uv add <pkg>` to add a dependency. Never use `pip install` or a `requirements.txt`.

## Checks

```bash
uv run ruff check .          # lint
uv run ruff format .         # format
uv run pytest                # tests (real-model tests skipped)
uv run pytest -m model       # slow tests with the real embedding model
```

Run lint, format and `uv run pytest` before opening a pull request. Generate test images with Pillow in fixtures; do not commit binary images.

## Hard rules

1. **Never delete user files** except in the Empty Trash code path (`app/services/trash.py`). Trashing means moving to `trash/` in the data folder. Folder import must never modify or delete source files.
2. **Never run the model in float16.** Use bf16 (if supported) or float32, and check embeddings for NaN.
3. **Fully offline at runtime.** No external API calls and no CDN assets; vendor what you need into `app/static/`. The only network use is the model download after the user clicks Download.
4. Bind to `127.0.0.1` by default.
5. Use `uv` for everything.
6. Keep thresholds and scoring weights in config/settings, not hardcoded inline.

## Conventions

- Python 3.11+ with type hints everywhere; `ruff` for lint and format.
- Business logic lives in `app/services/`; routes stay thin.
- The embedder sits behind an interface so tests can inject a fake and avoid loading the model.
- Small commits; update `docs/TASKS.md` when a task is done.

## Pull requests

Describe what changed and why, and link the issue. Do not include personal photos, paths or logs in issues, tests or screenshots. Use [`scripts/make_demo_library.py`](scripts/make_demo_library.py) with photos you have the rights to publish.
