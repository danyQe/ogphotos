# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-23

First public release.

### Added

- Import a Google Takeout export by folder or upload; the source is only ever read.
- Exact-duplicate detection (SHA-256) and near-duplicate detection (pHash).
- Similar-shot groups from EmbeddingGemma 2 embeddings, with a suggested best photo.
- Keyboard-driven review, trash, restore and Empty trash (the only action that deletes files).
- Faces and People with InsightFace buffalo_l (optional download, non-commercial use only).
- Date-sorted library with year/month filters; dates from EXIF and Takeout sidecars.
- First-run setup: data-folder picker and a models page with license acceptance, downloads and offline zip install.
- Installers: Windows (`OGphotos-Setup-<version>.exe`, x86_64), macOS (`OGphotos-<version>.dmg`, Apple Silicon), Linux (`ogphotos-<version>-linux.tar.gz`, x86_64 and aarch64).
- Fully offline after setup: local-only server, no update checks, no telemetry.
- Automatic migration of the config and library from LocalLens, the earlier name.

### Known issues

- Look-alike people may be merged into one large People group.
- macOS is Apple Silicon only.
- The apps are unsigned; expect an OS warning on first launch.
