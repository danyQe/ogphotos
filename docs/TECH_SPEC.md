# Technical Spec — OGphotos

## 1. Stack

| Layer | Choice |
|-------|--------|
| Package manager | `uv` (pyproject.toml, `uv.lock`) |
| Python | 3.11+ |
| Web | FastAPI + Uvicorn |
| Templates/UI | Jinja2 templates + HTMX + small vanilla JS (no build step) |
| DB | SQLite (WAL mode) via SQLAlchemy 2.0 |
| Images | Pillow, `pillow-heif` (HEIC), `imagehash` (pHash) |
| Sharpness | `opencv-python-headless` (Laplacian variance) |
| Embeddings | `sentence-transformers`, `transformers`, `torch` — model `google/embeddinggemma-2` |
| Vector search | NumPy brute-force cosine in memory (10k × 512 float32 ≈ 20 MB — no FAISS needed) |
| Tests | pytest |

## 2. Project layout

```
ogphotos/
├── pyproject.toml
├── CLAUDE.md
├── docs/
├── app/
│   ├── main.py              # FastAPI app, startup (DB init, worker start, index load)
│   ├── config.py            # settings (paths, thresholds) via pydantic-settings
│   ├── db.py                # engine, session, WAL pragma
│   ├── models.py            # SQLAlchemy models
│   ├── routes/
│   │   ├── pages.py         # HTML pages
│   │   ├── upload.py        # upload API (folder import: importer.py)
│   │   ├── groups.py        # group review API
│   │   ├── files.py         # file/thumbnail/trash API
│   │   └── settings.py
│   ├── services/
│   │   ├── ingest.py        # hash, dedup check, store, metadata
│   │   ├── hashing.py       # sha256, phash
│   │   ├── metadata.py      # EXIF, Takeout JSON sidecar, dimensions
│   │   ├── thumbnails.py
│   │   ├── embedder.py      # EmbeddingGemma 2 wrapper (lazy singleton)
│   │   ├── vector_index.py  # in-memory matrix + id map
│   │   ├── grouping.py      # assign/merge/regroup
│   │   ├── scoring.py       # sharpness + best-pick
│   │   └── trash.py
│   ├── worker.py            # background analysis queue
│   ├── templates/
│   └── static/
├── data/                    # gitignored, created at runtime
│   ├── library/{year}/      # stored originals
│   ├── thumbs/
│   ├── faces/               # face crops {face_id}.jpg
│   ├── models/buffalo_l/    # face model (scripts/fetch_face_model.py)
│   ├── trash/
│   └── ogphotos.db
└── tests/
```

## 3. Data model

### `files`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| sha256 | TEXT UNIQUE NOT NULL | exact dedup key |
| phash | TEXT | 64-bit hex; NULL for videos |
| original_name | TEXT | |
| stored_path | TEXT | relative to `data/` |
| media_type | TEXT | `image` / `video` |
| mime | TEXT | |
| size_bytes | INTEGER | |
| width, height | INTEGER | |
| duration_s | REAL | videos only (MP4/MOV header); NULL otherwise |
| taken_at | DATETIME | EXIF / MP4 `mvhd` → sidecar → mtime |
| taken_at_source | TEXT | `exif` / `metadata` / `sidecar` / `mtime` |
| uploaded_at | DATETIME | |
| sharpness | REAL | Laplacian variance on downscaled grayscale |
| embedding | BLOB | float32, 512d, L2-normalized |
| analysis_status | TEXT | `pending` / `done` / `failed` / `skipped` |
| analysis_error | TEXT | |
| group_id | INTEGER FK → groups.id NULL | |
| is_trashed | BOOLEAN | |
| trashed_at | DATETIME | |

Indexes: `sha256` (unique), `group_id`, `taken_at`, `analysis_status`, `is_trashed`.

### `groups`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| best_file_id | INTEGER FK | suggested best |
| size | INTEGER | cached count of non-trashed members |
| reviewed | BOOLEAN | |
| created_at, updated_at | DATETIME | |

### `group_exclusions`
`(file_id_a, file_id_b)` pairs the user marked "not similar"; grouping must never re-join them.

### `settings`
Key/value: `phash_max_distance` (default 8), `embed_min_cosine` (default 0.92), `embed_dim` (512).

## 4. Ingest pipeline

### 4.1 Synchronous (inside the upload request, per file)
1. Stream upload to a temp file in `data/tmp/`, computing SHA-256 while writing.
2. **Exact check:** `SELECT id FROM files WHERE sha256 = ?`.
   - If hit (including trashed files) → delete temp, return `exact_duplicate` + existing file id. If the existing one is trashed, say so.
   - If the stored copy's date is only a guess (`mtime` / `upload`) and this copy came with a sidecar, update the stored row's `taken_at` from the sidecar (`date_fixed: true`). Re-importing a Takeout folder thus repairs dates of photos first uploaded without their `.json`. The stored file is not moved.
3. Validate type (open with Pillow / sniff video mime). Fail → `error`.
4. Read metadata: dimensions, EXIF DateTimeOriginal. Takeout sidecar: if a JSON named like `<file>.json` or `<file>.supplemental-metadata.json` was uploaded in the same batch or exists next to the source file in folder import, read `photoTakenTime.timestamp`. `X-edited.jpg` has no sidecar of its own; it uses `X.jpg`'s. Videos: MP4/MOV `mvhd` creation time (`metadata`) → sidecar → mtime; `mvhd` also gives `duration_s`.
5. Compute pHash (images only).
6. **Near check:** compare against all stored pHashes (load into memory as a NumPy uint64 array; Hamming distance via bit counting). Matches with distance ≤ `phash_max_distance` → status `similar_exists`, return up to 5 match ids.
7. Move file to `data/library/{year}/{stem}__{sha256[:8]}{ext}`. Generate thumbnail (400px long edge, JPEG, in `data/thumbs/{id}.jpg`). Apply EXIF orientation for thumbs. Videos get a poster frame via OpenCV (fallback tile "▶ name" if no frame is readable); `services/video_backfill.py` fills duration, date and thumbnail for videos stored earlier (one pass at startup).
8. Insert row with `analysis_status = pending` (videos: `skipped`), enqueue for the worker.
9. Return result.

Uploads accept multiple files per request (`multipart/form-data`); the frontend sends batches of ~20.

### 4.2 Background worker
- Single worker thread consuming a queue; on startup also enqueues all `pending` rows (resumability).
- Batch of N images (8–32 depending on device):
  1. Compute embedding (see §5).
  2. Compute sharpness.
  3. Store, add to vector index.
  4. Run grouping assignment (§6).
- Exposes progress: `{pending, done, failed}` via `/api/status`.

### 4.3 Folder import
`POST /api/import-folder {path}` starts one background job (one at a time). It walks the folder (sorted, hidden files skipped), reads the `.json` sidecars of each directory, and runs every other file through §4.1 with the file's mtime. Sources are opened read-only and copied; nothing in the source folder is changed. Paths that contain or sit inside the data folder are refused. Non-media files come back as `error` rows.

## 5. Embeddings — EmbeddingGemma 2

From the model card (keep in `docs/embeddinggemma-2-model-card.md`):

- Load text + vision only:
  `SentenceTransformer("google/embeddinggemma-2", config_kwargs={"audio_config": None}, model_kwargs={"torch_dtype": dtype})`
- **dtype:** `bfloat16` if `torch.cuda.is_available() and torch.cuda.is_bf16_supported()`, else `float32`. **Never float16** — produces NaN/silently degraded embeddings.
- Images get **no text prefix**.
- Use `truncate_dim=512, normalize_embeddings=True`. 512d is near-lossless for images; do not use 128d (degrades multimodal quality significantly).
- Image input format — **verified** with `scripts/embed_smoke.py` (sentence-transformers 6.1, transformers 5.19): `model.encode([{"image": pil_image}, ...], truncate_dim=512, normalize_embeddings=True)`. A bare PIL image gives identical vectors; we use the dict form. Requires `torchvision` (the Gemma 4 image processor imports it).
- Vision token budget: set `model[0].processor.image_processor.max_soft_tokens` (default 280). Measured on a mid-range laptop CPU: 280 → 16 s/img, 140 → 7 s/img, 70 → 3.7 s/img; 140 vs 280 similarity matrices correlate at 0.994. Default 140 (`embed_image_tokens`).
- Guard: if any embedding contains NaN, mark file `failed` with error and log loudly.
- Offline: the repo is public (Apache-2.0, no login). Download once via `scripts/embed_smoke.py`; at runtime the model loads with `local_files_only=True`. If it can't load, files stay `pending` and `/api/status` reports the error.
- Lazy-load the model on first use, not at import time, so the server starts fast.
- Device: CUDA → MPS (Apple) → CPU. If MPS gives errors with bf16, use float32. On a GPU the worker batches `embed_batch_size_gpu` photos (halved on out-of-memory); photos are decoded on `decode_workers` threads so the GPU is not left waiting. GPU builds of torch are installed only where usable (see packaging/launch.*).

## 6. Grouping

Two photos are **similar** if:
`phash_distance ≤ phash_max_distance` **OR** `cosine(embedding) ≥ embed_min_cosine`
…and the pair is not in `group_exclusions`.

### Incremental assignment (per newly analyzed file F)
1. Find all neighbors of F above threshold (vector index + pHash).
2. No neighbors → no group.
3. Neighbors have no group → create group with F + ungrouped neighbors, closest first, adding a neighbor only if it is similar to everyone already in the new group.
4. Neighbors in groups → F may join a group only if it is similar to **every** live member (complete linkage). Among eligible groups, pick the highest mean similarity.
5. No eligible group → F stays ungrouped (or starts a new group per 3). Do **not** auto-merge groups.

_Why complete linkage:_ the original rule ("join if similar to any member") chained A≈B≈C≈D on a real ~3k-photo library: very large groups with member pairs at cosine 0.79 and one group spanning years. "Similar to the best" still drifted (pairs at 0.86) because the best changes as members join. With complete linkage every pair in a group is similar: groups stay small and each spans minutes.
Existing analyzed files are grouped once by a backfill in `taken_at` order (settings key `grouping_backfilled`).
6. Recompute group `best_file_id` and `size`; set `reviewed = false` if a new member joined.

### Regroup all (Settings button)
Clear all `group_id`s and rebuild with the current thresholds: process analyzed files sorted by `taken_at` through the same complete-linkage assignment as above. Preserve exclusions. Reviewed flags are lost after regroup; warn the user. Runs on a background thread holding `Grouper.lock`; the Settings page polls `GET /api/settings` → `regroup`.

Single-member groups are deleted.

## 7. Best-photo scoring

Within a group, score each file:
```
score = 0.5 * norm(sharpness) + 0.3 * norm(width*height) + 0.2 * norm(size_bytes)
```
`norm` = min-max within the group. Ties → earliest `taken_at`. Prefer non-"-edited" originals only if scores are equal (configurable later). Keep this in one function so weights are easy to tweak.

## 8. Trash

- Trash = move file from `library/` to `trash/` (same relative path), set `is_trashed`, `trashed_at`. Remove from vector index and pHash array; update group size/best.
- A trashed file leaves its group (`group_id` cleared). A group left with fewer than 2 live members dissolves, so "Keep best" ends the group; restoring the others re-forms it.
- Restore = move back, re-add to indexes, re-run grouping assignment. Never overwrites: if the library path is taken, the file comes back as `name-1.ext`. Files that weren't fully analyzed are re-queued.
- "Not similar" adds exclusions between the file and every other member, then re-places the file (it may join or start another group, never this one).
- All group writes (worker, review actions, trash) serialize on `Grouper.lock`.
- Empty trash = permanently delete files in `trash/` and their DB rows and thumbnails. Requires `confirm=true` param. Rows go first, then files; a path that resolves outside `trash/` is refused.
- No other code path may call `os.remove` / `unlink` on library files.

## 9. API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/upload` | multipart files (+ optional sidecar JSONs) → per-file results |
| POST | `/api/import-folder` | `{path}` → starts import job, returns job id |
| GET | `/api/import-folder/{job_id}` | progress + results (`?offset=` returns only newer rows) |
| POST | `/api/import-folder/{job_id}/cancel` | stop after the current file |
| GET | `/api/status` | worker progress, counts |
| GET | `/api/files` | list, filters: `year`, `month`, `trashed`, paging |
| GET | `/api/files/{id}` | metadata |
| GET | `/api/files/{id}/thumb` | thumbnail |
| GET | `/api/files/{id}/original` | original file |
| GET | `/api/files/{id}/similar` | top-k similar |
| GET | `/api/groups` | list, filters: `reviewed`, `year`, sort by size |
| GET | `/api/groups/{id}` | members with scores |
| POST | `/api/groups/{id}/keep-best` | trash all except best |
| POST | `/api/groups/{id}/apply` | `{keep: [ids], trash: [ids]}` |
| POST | `/api/groups/{id}/remove/{file_id}` | split out + add exclusions |
| POST | `/api/groups/{id}/reviewed` | mark reviewed |
| POST | `/api/files/{id}/trash` / `/restore` | |
| POST | `/api/trash/empty?confirm=true` | permanent delete |
| GET/PUT | `/api/settings` | thresholds |
| POST | `/api/regroup` | full regroup |
| GET | `/api/people`, `/api/people/{id}`, `/api/people/{id}/faces` | people list / person photos / person faces |
| PATCH | `/api/people/{id}` | `{name?, hidden?, cover_face_id?}` |
| POST | `/api/people/{id}/merge` | `{into_person_id}` |
| GET | `/api/faces/unsorted`, `/api/faces/{id}/crop`, `/api/files/{id}/faces` | unassigned faces / crop JPEG / faces on a photo |
| POST | `/api/faces/{id}/remove`, `/api/faces/assign` | not this person / assign to person or new |
| POST | `/api/faces/recluster`, `/api/faces/retry-failed` | background recluster / re-queue failed |
| PUT | `/api/faces/scanning` | `{enabled}` |

Upload result item:
```json
{
  "filename": "IMG_1234.jpg",
  "status": "new | exact_duplicate | similar_exists | error",
  "file_id": 123,
  "existing_file_id": 45,
  "existing_is_trashed": false,
  "date_fixed": false,
  "similar_file_ids": [45, 67],
  "message": "Exact copy of IMG_1234.jpg (2021)"
}
```

## 10. UI pages

- `/` → redirect to `/groups` if library non-empty, else `/upload`.
- `/upload` — drag-drop zone, folder picker (`<input webkitdirectory>`), batch uploader with live result list and summary counters, background analysis progress bar (poll `/api/status` every 2s).
- `/import` — path input + progress.
- `/groups` — cards: thumbnail strip, count, date range, reviewed badge.
- `/groups/{id}` — large thumbnails side by side, metadata under each, ⭐ best badge, click toggles keep/trash (trash = dimmed + red border). Shortcuts: `K` keep best & next, `Enter` apply selection, `→`/`←` next/prev group, `1–9` toggle photo n. `Enter` with nothing marked = mark reviewed and go next.
- `/library` — date-sorted grid, year/month filter, lazy-loading thumbnails.
- `/trash` — grid, restore, empty trash.
- `/settings` — thresholds, regroup button, model/device info.

Keep styling minimal: one CSS file, dark-mode friendly, CSS grid.

## 11. Config

`app/config.py` with pydantic-settings, env prefix `OGPHOTOS_`:
`DATA_DIR`, `HOST=127.0.0.1`, `PORT=8000`, `EMBED_BATCH_SIZE=16`, `EMBED_DIM=512`, `DEVICE=auto`.
Bind to `127.0.0.1` only by default.

### 11.1 Library location (first run)
`services/locations.py` resolves the data folder: `OGPHOTOS_DATA_DIR` → `data_dir` in the per-user config file (`%APPDATA%\OGphotos\config.json` / `~/Library/Application Support/OGphotos/config.json` / `$XDG_CONFIG_HOME/ogphotos/config.json`) → none, in which case the app starts in **setup mode**: only `/setup`, `/api/setup*`, `/api/browse-folder` and `/static/` answer (other pages redirect to `/setup`, other APIs return 503). The default suggestion is the OS Pictures folder + `OGphotos` (Windows `FOLDERID_Pictures` via `SHGetKnownFolderPath`, so OneDrive redirection is honoured; Linux `xdg-user-dir PICTURES` / `user-dirs.dirs`; else `~/Pictures`). A non-empty folder that isn't an OGphotos library gets an `OGphotos` subfolder. A pre-setup `./data` library can be kept, or moved by a single directory rename (same drive only; nothing is copied or deleted). After `POST /api/setup` the config is written and the services start in-process. `POST /api/browse-folder` opens a native Tk folder dialog on the local machine (501 when there is no display).

### 11.2 Cross-site requests
The server only listens on localhost, but any web page in the browser can still send requests to it. Non-GET requests whose `Origin` differs from the `Host`, or marked `Sec-Fetch-Site: cross-site`, are refused with 403.

## 12. Testing

- Unit: sha256/pHash, metadata fallback order, Takeout sidecar parsing, scoring, grouping rules (incl. exclusions and no-chaining), trash/restore moves.
- Integration: upload the same file twice → second is `exact_duplicate`; upload resized copy → `similar_exists`.
- Embedding tests are marked `@pytest.mark.model` and skipped by default (slow); a fake embedder is injected for the rest.
- Fixtures: generate test images with Pillow (solid shapes, resized/recompressed variants) — no binary fixtures in the repo.

## 13. Faces & people


- **Model**: InsightFace `buffalo_l` (SCRFD-10G detector + ArcFace R50, 512-d) on onnxruntime: CUDA (onnxruntime-gpu, reusing the CUDA libraries of the cu130 torch) or DirectML when that build is installed, else CPU; CoreML only with `device=mps`. Installed once with `uv run python scripts/fetch_face_model.py` (`--zip PATH` reuses a download) into `{data_dir}/models/buffalo_l/`; the app never downloads. Weights are non-commercial only. Without the model everything else works and the Settings page shows the install command.
- **Tables**: `faces` (box as fractions of the upright photo, `det_score`, float32 embedding, `person_id`, `assigned_by` auto/user), `people` (`label_no` sequential, never reused; `name`; `cover_face_id`; cached `face_count`; `hidden`), `face_exclusions` (`face_id`, `person_id` = "not this person"); `files` gains `face_status`, `face_error`, `face_count`. Crops live in `{data_dir}/faces/{face_id}.jpg` and are deleted only by Empty Trash.
- **Pipeline**: `FaceWorker` polls `face_status = pending` images (backfill resumes after restart), detects on the EXIF-upright image (long side <= `face_det_max_side`), keeps faces with score >= `face_min_score` and short side >= `face_min_px`, aligns to 112x112, embeds, L2-normalizes (NaN/zero-norm dropped), writes rows and crops.
- **Clustering** (`PeopleClusterer`, one lock): incremental assignment votes among the top `face_neighbors` neighbours with cosine >= `face_match_cosine` (respecting exclusions); unassigned faces form a new person once >= `face_min_cluster` agree. Full recluster keeps user-assigned faces and named people fixed and runs DBSCAN-style clustering (`eps = 1 - face_match_cosine`) on the rest, reusing old unnamed person ids. Lock order: `Grouper.lock` then `PeopleClusterer.lock`; `FaceWorker._busy` then `PeopleClusterer.lock`.
- **Tunables** (Settings page): `face_min_score` 0.6, `face_min_px` 60, `face_match_cosine` 0.45, `face_min_cluster` 3.
- **UI**: `/people` (grid, inline rename, Unsorted, Show hidden), `/people/{id}` (Photos / Faces tabs, merge, hide, cover), `/people/unsorted`, face chips on the photo detail page, Settings -> Faces (progress, retry, scanning switch, recluster).
- **API**: rows added to section 9.

### 13.1 Threshold tuning (real library)
Tuned on a real ~3k-photo library with a partial face scan (CPU backfill runs at roughly 25-75 photos/min on a mid-range laptop). Detection and embeddings behaved as expected (clear portraits found at score 0.8-0.9; same person cos ~0.44, different people < 0.2). `face_match_cosine` values 0.40, 0.45, 0.50, 0.55 and 0.60 were compared: higher values assign fewer faces and the number of people stays in a similar range, while the biggest clusters shrink only modestly.

Spot check of random crops from the biggest clusters: at 0.40 the two biggest clusters visibly mix several people, and the biggest cluster still mixes similar-looking people at 0.50 and at 0.60. So single-link DBSCAN chaining through a few near-identical faces, not the threshold, causes the mixing within the 0.40-0.60 range; raising the value only trades fewer assigned faces for little gain. Same-person splits were not systematically counted. Default kept at 0.45. This is a judgment call on a partial scan; use "Not this person" / Merge, and re-tune on your own library. Possible follow-up: stricter linkage (e.g. require agreement with a cluster centroid) for very large clusters.
