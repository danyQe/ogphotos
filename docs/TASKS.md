# Build Plan — OGphotos

Work milestone by milestone. Each milestone ends with passing tests and a working app. Check boxes as you go.

## M0 — Scaffold
- [x] `uv init`, add deps: fastapi, uvicorn[standard], jinja2, python-multipart, sqlalchemy, pydantic-settings, pillow, pillow-heif, imagehash, numpy, opencv-python-headless; dev: pytest, httpx
- [x] Project layout per TECH_SPEC §2; `data/` gitignored and auto-created on startup
- [x] SQLite with WAL, models from TECH_SPEC §3, create tables on startup
- [x] Base template + nav (Upload, Groups, Library, Trash, Settings)
- **Done when:** `uv run uvicorn app.main:create_app --factory --reload` serves an empty UI; `uv run pytest` passes.

## M1 — Upload + exact dedup
- [x] Streaming upload with SHA-256 computed while writing
- [x] Exact-duplicate check (including trashed files)
- [x] Metadata: dimensions, EXIF date, Takeout JSON sidecar, mtime fallback; HEIC support
- [x] Store to `library/{year}/`, generate thumbnail (respect EXIF orientation)
- [x] Upload page: drag-drop + folder picker, batches of 20, live result rows, summary counters
- [x] Videos stored with exact dedup only (`analysis_status = skipped`)
- **Done when:** uploading the same file twice reports `exact_duplicate` and stores one copy.

## M2 — Near-duplicate (pHash)
- [x] pHash on ingest; in-memory uint64 array, Hamming search
- [x] `similar_exists` status with match thumbnails in the upload results
- **Done when:** a resized/recompressed copy reports `similar_exists`.

## M3 — Embeddings + background worker
- [x] Add torch, transformers, sentence-transformers (+ torchvision)
- [x] **First:** smoke script `scripts/embed_smoke.py` that loads EmbeddingGemma 2 (text+vision only), embeds 2 images, prints cosine. Confirm the correct image input format and no NaNs.
- [x] `embedder.py` lazy singleton, device/dtype selection (bf16 or fp32 — never fp16), 512d normalized
- [x] Worker thread + queue, resume `pending` on startup, `/api/status`, progress bar in UI
- [x] In-memory vector index; add/remove
- [x] Sharpness score
- **Done when:** after upload, all images reach `done` and `/api/files/{id}/similar` returns sensible neighbors.

## M4 — Grouping
- [x] Incremental assignment rules (TECH_SPEC §6), no auto-merge (complete linkage)
- [x] Best-photo scoring (§7)
- [x] Groups list page + group detail page
- **Done when:** burst shots uploaded together appear as one group with a ⭐ best.

## M5 — Review + trash
- [x] Keep/trash toggles, "Keep best, trash rest", apply selection, "Not similar" (exclusions)
- [x] Mark reviewed, next/prev navigation, keyboard shortcuts
- [x] Trash page: restore, empty trash with confirm
- [x] Indexes and groups updated on trash/restore
- **Done when:** I can clean a group in one keypress and restore it from Trash.

## M6 — Bulk import + settings
- [x] Folder import job (walk path, pick up sidecars next to files, never modify source)
- [x] Settings page: thresholds, regroup all (with warning), model/device info
- [x] Library page: date grid, year/month filters, lazy-loading
- **Done when:** importing a full Takeout year folder works end to end.

## M7 — Finishing touches
- [x] First-run setup page: choose the library folder (default: OS Pictures folder), native Browse dialog, saved in the per-user config
- [x] Existing `./data` library: move it (same-drive rename), keep it, or start fresh
- [x] Refuse cross-site write requests to the local server
- **Done when:** a fresh install asks where to keep photos and never writes into the project folder.

## M8a — Videos in the library
- [x] V1: read creation time and duration from MP4/MOV headers
- [x] V2: poster-frame thumbnails and startup backfill
- [x] V3: poster tiles with duration badge in the library
- **Done when:** videos show poster frames and `m:ss` badges in date order among photos.

## M8 — Faces & people
- [x] 1. Face tables, migration and settings
- [x] 2. Face engine (SCRFD + ArcFace via onnxruntime) and model fetch script
- [x] 3. Face geometry, crops and in-memory face index
- [x] 4. Face worker (pending-file backfill, retry, enable/disable)
- [x] 5. Incremental assignment and full recluster
- [x] 6. Trash and restore hooks (Empty Trash removes crops)
- [x] 7. People service (rename, merge, hide, cover, remove, assign)
- [x] 8. People and faces API
- [x] 9. People pages and face chips on photos
- [x] 10. Faces section on the Settings page
- [x] 11. Status and banners
- [x] 12. Tests with a fake face engine and a real-model test
- [x] 13. Docs, real-library run, threshold tuning
- **Done when:** photos are scanned for faces offline and people can be browsed, named and merged.

## Phase 2 (later)
- Text search box using EmbeddingGemma text queries (`SearchQuery` prompt) against image embeddings
- Video similarity via frame embeddings
- Live Photo pairing (HEIC + MP4)
