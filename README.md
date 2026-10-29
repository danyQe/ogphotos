# OGphotos

OGphotos — clean up your Google Takeout export. Duplicates, similar shots, faces. Works offline after a one-time model download.

<!-- Screenshots and a short GIF go in docs/images/ (recorded from the demo library built by
     scripts/make_demo_library.py). Placeholder until the owner adds them:
     ![Groups page](docs/images/groups.png)  ![Review demo](docs/images/review.gif) -->

*Screenshots: coming soon (`docs/images/`).*

## Download

Website: <https://danyqe.github.io/ogphotos>

| Platform | File | Notes |
|----------|------|-------|
| Windows (x64 only; not Windows on ARM) | `OGphotos-Setup-<version>.exe` | Installer |
| macOS 14+ (Apple Silicon only) | `OGphotos-<version>.dmg` | Drag to Applications |
| Linux (x86_64 and aarch64) | `ogphotos-<version>-linux.tar.gz` | Extract and run |

Get them from the [latest release](https://github.com/danyQe/ogphotos/releases/latest).

## Opening the app the first time

The downloads are not code-signed yet, so your OS will warn you once.

- **Windows:** SmartScreen shows "Windows protected your PC". Click **More info**, then **Run anyway**.
- **macOS:** right-click the app and choose **Open**. If it is still blocked, open **System Settings → Privacy & Security** and click **Open Anyway**.
- **Linux:** extract the tarball (`tar xzf ogphotos-<version>-linux.tar.gz`), run the launcher inside, and accept the prompt offering to add OGphotos to your applications menu.

## What the first run does

1. A launch script prints `Setting up OGphotos (one-time download of about 1 GB; usually 5–30 min)…` and installs its own Python environment. On a PC with an NVIDIA GPU (driver 580 or newer, GeForce RTX 20 series / GTX 16 series or newer) it says "for your NVIDIA GPU" instead and downloads the CUDA builds (about 4 GB), so photo analysis and face scanning run on the GPU; everything else uses the CPU builds. Macs with Apple Silicon use their GPU either way. Set `OGPHOTOS_TORCH=cpu` (or `cuda`) before launching to override the detection. This happens once. Later launches start in seconds.
2. Your browser opens. The **setup page** asks where to keep your photos, library and models (the *data folder*).
3. The **models page** (`/setup/models`) shows each model's license. Accept and download:
   - **EmbeddingGemma 2** — required, Apache-2.0, about 1.5 GB. Finds similar photos.
   - **InsightFace buffalo_l** — optional, about 290 MB. Powers faces and People. Its weights are for **non-commercial research use only**.
   - On a machine without internet, use **Install from a zip file** instead.
4. After that, everything runs offline.

Then open **Import** and pick your Takeout `Google Photos` folder. OGphotos only reads it.

## Features

- **Exact duplicates** by SHA-256, including Takeout's year-folder plus album-folder copies. Nothing is stored twice.
- **Near-duplicates** by perceptual hash, shown as thumbnails of what they resemble.
- **Similar-shot groups** from EmbeddingGemma 2 embeddings (cosine similarity) plus pHash. Every photo in a group must be similar to every other. A suggested best photo is starred (sharpness, resolution, file size).
- **Fast review:** click or press `1`–`9` to mark photos for trash, `Enter` to apply, `K` to keep the best and trash the rest. **Not similar** splits a photo out for good.
- **Faces and People:** faces are found and clustered with InsightFace through onnxruntime, on the GPU when your install has a GPU build (NVIDIA CUDA or DirectML) and on the CPU otherwise. Rename, merge, hide or reassign people.
- **Library:** every photo by date taken with year/month filters. Dates come from EXIF, then Takeout `.json` / `.supplemental-metadata.json` sidecars, then file time.
- **Safe trash:** trashed photos are moved to `trash/` in your data folder. **Empty trash** is the only action that deletes files.
- **Formats:** JPG, PNG, HEIC/HEIF, WEBP, GIF; MP4 and MOV videos (exact duplicates only).
- **Tunable:** similarity thresholds can be changed in Settings, then **Regroup all**.

OGphotos never modifies or deletes the folder you import from.

## Privacy

After setup, nothing leaves your machine. The app listens on `127.0.0.1` only, makes no update checks and sends no telemetry. The only network use is the model download you start yourself on the models page.

Where things live:

| What | Windows | macOS | Linux |
|------|---------|-------|-------|
| App home (Python env, cache, logs) | `%LOCALAPPDATA%\OGphotos` | `~/Library/Application Support/OGphotos` | `~/.local/share/ogphotos` |
| Config | `%APPDATA%\OGphotos\config.json` | `~/Library/Application Support/OGphotos/config.json` | `~/.config/ogphotos/config.json` |

Logs are at `<app home>/logs/ogphotos.log`. Your photos, library, trash, database and models live in the data folder you chose on the setup page.

## Known issues

- Look-alike people (for example family members) may be merged into one large People group. Use rename, merge and "not this person" to fix them, or tune the match threshold in Settings.
- macOS builds need macOS 14 (Sonoma) or later on Apple Silicon.
- The apps are unsigned, so you will see an OS warning on first launch (see above).

## Upgrade

Install the new version over the old one. The next launch re-syncs only what changed. Your data and config are untouched. If you used LocalLens (the earlier name), your config and library are migrated automatically.

## Uninstall

- **Windows:** use Apps and features. The uninstaller offers to remove the Python environment and cache. It never removes your photos, library or models. It also leaves the config file `%APPDATA%\OGphotos\config.json` (it remembers your library folder); delete it too for a clean removal.
- **macOS:** delete OGphotos from Applications, then delete `~/Library/Application Support/OGphotos`. That folder also holds the config file (`config.json`).
- **Linux:** delete the extracted folder, `~/.local/share/applications/ogphotos.desktop` and `~/.local/share/ogphotos`. The config file `~/.config/ogphotos/config.json` is left behind too; delete it for a clean removal.

Your data folder is yours to keep or delete.

## Build from source

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync && uv run python -m app.launcher
```

On an NVIDIA machine, install the CUDA builds instead: `uv sync --no-default-groups --group dev --group cuda`, then use `uv run --no-sync` (a plain `uv run` switches back to the CPU group).

For development, run the server with auto-reload instead (http://127.0.0.1:8000):

```bash
uv run uvicorn app.main:create_app --factory --reload
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for tests and conventions, [docs/TECH_SPEC.md](docs/TECH_SPEC.md) for the design, and [docs/RELEASING.md](docs/RELEASING.md) for cutting a release. Settings are environment variables with the `OGPHOTOS_` prefix; see `app/config.py`.

To make screenshots without personal photos, build a fake Takeout from photos you may publish (CC0 or your own):

```bash
uv run python scripts/make_demo_library.py --src SAMPLE_PHOTOS --out DEMO_TAKEOUT
```

## License

[MIT](LICENSE). Third-party components and model licenses are listed in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md). The InsightFace model weights are non-commercial research use only.

---

Not affiliated with Google. Google Photos is a trademark of Google LLC.
