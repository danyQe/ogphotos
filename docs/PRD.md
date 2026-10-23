# PRD — OGphotos: Offline Photo Dedup & Sorter

## 1. Problem

I exported 5k–10k photos/videos from Google Photos via Takeout (already split into year folders). Many are duplicates or near-identical shots (bursts, re-saves, WhatsApp copies, album copies Takeout duplicated across folders). I want one local app to ingest them, tell me what already exists, group similar shots together, and let me quickly keep the best and delete the rest.

## 2. Goals

1. **Upload with instant feedback** — for each uploaded file, tell the user: *new*, *exact duplicate already exists*, or *similar image already exists*.
2. **Group similar photos** — burst shots and near-identical photos appear together in one group.
3. **Fast cleanup** — review a group, see a suggested "best" photo, keep it, trash the rest in one click.
4. **100% local** — no cloud calls at runtime. Model weights downloaded once, then offline.
5. **Safe** — nothing is ever hard-deleted without an explicit "Empty trash" action.

## 3. Non-goals (v1)

- Face recognition / people grouping (Phase 2).
- Similarity grouping for videos (v1 does exact-duplicate detection only for videos).
- Multi-user, auth, remote access, mobile app.
- Photo editing.

## 4. Users

Single user on their own laptop/desktop, accessing `http://localhost:8000`.

## 5. Core user flows

### 5.1 Upload
1. User opens the Upload page and drags in files or a whole folder (browser folder picker).
2. Files upload in batches. For each file, a result row appears:
   - ✅ **New** — stored in library.
   - 🟰 **Exact duplicate** — identical file already in library; not stored again. Shows a thumbnail of the existing file.
   - ≈ **Similar exists** — stored, but flagged with thumbnails of the closest existing matches.
   - ⚠️ **Error / unsupported** — with reason.
3. Summary bar: `1,240 uploaded · 312 new · 801 exact dupes skipped · 127 similar`.
4. Heavy work (embeddings) continues in the background; a progress indicator shows "Analyzing 45 / 312".

### 5.2 Server-side folder import (for bulk)
Uploading 10k files through the browser is slow. User can also paste a local folder path (e.g. the Takeout folder) on the Import page; the server walks it and runs the same pipeline, copying files into the library. Same result reporting.

### 5.3 Review groups
1. Groups page lists similar-photo groups, largest first (filter: unreviewed / reviewed / year).
2. Opening a group shows all photos side-by-side with: resolution, file size, date, sharpness score, and a **⭐ Suggested best** badge.
3. User actions:
   - Click to toggle keep/trash per photo.
   - **"Keep best, trash rest"** (one click / keyboard `K`).
   - **"Not similar"** — remove a photo from the group (split it out).
   - **"Mark reviewed"** and go to next group (`→`).
4. Trashed photos move to Trash; group marked reviewed.

### 5.4 Browse library
Grid of all non-trashed photos, filter by year/month, sorted by date taken. Click opens a larger view with metadata and "show similar".

### 5.5 Trash
List trashed files; **Restore** individual / all; **Empty trash** (confirmation dialog with count) permanently deletes.

## 6. Functional requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| F1 | Accept JPG, PNG, HEIC/HEIF, WEBP, GIF; videos MP4, MOV (stored, exact-dedup only) | P0 |
| F2 | Exact duplicate detection via SHA-256 of file bytes; duplicates are not stored | P0 |
| F3 | Near-duplicate detection via perceptual hash (pHash) | P0 |
| F4 | Semantic similarity via EmbeddingGemma 2 image embeddings | P0 |
| F5 | Automatic grouping of similar photos | P0 |
| F6 | Suggested best photo per group (sharpness, resolution, size) | P0 |
| F7 | Trash with restore; permanent delete only via Empty Trash | P0 |
| F8 | Read date taken from EXIF; fall back to Takeout JSON sidecar (`photoTakenTime`); fall back to file mtime | P0 |
| F9 | Server-side folder import | P1 |
| F10 | Adjustable similarity thresholds in Settings, with "Regroup all" | P1 |
| F11 | Keyboard shortcuts on review page | P1 |
| F12 | Text search ("dog on beach") using the shared text-image embedding space | P2 |
| F13 | Face detection + people clustering (InsightFace) | Phase 2 |

## 7. Non-functional requirements

- **Offline:** after first model download, runs with `HF_HUB_OFFLINE=1`.
- **Performance:** exact/near-dup verdict returned in the upload response (< 1s per file). Embeddings processed in background; 10k photos analyzed in a few hours on CPU, much faster on GPU.
- **Resumable:** if the server stops mid-analysis, pending files are picked up on restart.
- **Data integrity:** originals are copied into the library unmodified. Source folders (for folder import) are never modified or deleted.
- **Footprint:** load only text + vision encoders (~440M params). No audio encoder.

## 8. Success metrics

- All exact duplicates from a Takeout export are detected (0 missed).
- Burst/near-identical shots land in the same group in ≥ 90% of cases on a manual spot-check of 50 groups.
- User can review and clean 100 groups in under 15 minutes.

## 9. Phase 2 ideas

Faces/people, video similarity (frame embeddings), text search UI, Live Photo pairing (HEIC + MP4), export cleaned library back to year folders.
