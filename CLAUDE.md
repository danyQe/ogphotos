# CLAUDE.md — OGphotos

Local, offline web app (FastAPI on localhost) to upload photos, detect exact and similar duplicates, group similar shots, and clean them up. Single user, ~5k–10k photos from a Google Takeout export.

## Read first
- `docs/PRD.md` — what and why
- `docs/TECH_SPEC.md` — how (schema, pipeline, API, grouping rules)
- `docs/TASKS.md` — build order; work one milestone at a time and tick boxes
- `docs/embeddinggemma-2-model-card.md` — embedding model details

## Commands
```bash
uv sync                                   # install
uv run uvicorn app.main:create_app --factory --reload   # run at http://127.0.0.1:8000
uv run pytest                             # tests (model tests skipped)
uv run pytest -m model                    # slow tests with the real model
uv run python scripts/fetch_face_model.py   # one-time face model install
uv add <pkg>                              # add a dependency (never pip install)
```

## Hard rules
1. **Never delete user files** except in the Empty Trash code path (`services/trash.py`). Trashing = moving to `data/trash/`. Folder import must never modify or delete source files.
2. **Never run the model in float16.** bf16 (if supported) or float32 only. Check embeddings for NaN.
3. **Fully offline at runtime.** No external API calls, no CDN assets — vendor HTMX into `app/static/`.
4. Bind to `127.0.0.1` by default.
5. Use `uv` for everything. No `requirements.txt`, no bare `pip`.
6. Keep thresholds and scoring weights in config/settings, not hardcoded inline.

## Conventions
- Python 3.11+, type hints everywhere, `ruff` for lint/format.
- Business logic in `app/services/`; routes stay thin.
- Embedder behind an interface so tests can inject a fake (no model load in normal tests).
- Generate test images with Pillow in fixtures; don't commit binary images.
- Small commits per task; update `docs/TASKS.md` checkboxes when a task is done.

## Gotchas
- Takeout puts the same photo in year folders AND album folders → many exact dupes. Expected.
- Dates: EXIF often missing; read the Takeout `.json` / `.supplemental-metadata.json` sidecar (`photoTakenTime.timestamp`).
- HEIC needs `pillow_heif.register_heif_opener()` at startup.
- Apply EXIF orientation when making thumbnails (`ImageOps.exif_transpose`).
- The data folder is chosen on first run (setup page) and saved in the per-user config file, not in the repo; tests always pass an explicit `Settings(data_dir=tmp)`.
- Before writing `embedder.py`, verify the exact sentence-transformers call for image-only input with a smoke script — don't guess.
- Use `truncate_dim=512, normalize_embeddings=True`. Don't use 128d for images.
- Face boxes are fractions of the upright photo (`files.width/height` are pre-rotation).
- InsightFace weights are non-commercial only.
- torch/torchvision/onnxruntime live in two conflicting dependency groups, `cpu` (default) and `cuda` (cu130 torch + onnxruntime-gpu, CUDA 13). The launchers pick `cuda` when `nvidia-smi` reports driver 580+ and compute capability 7.5+, else `cpu`; `OGPHOTOS_TORCH` overrides. Dev GPU: `uv sync --no-default-groups --group dev --group cuda`, then `uv run --no-sync`. The app itself always tries GPU first and falls back to CPU.
