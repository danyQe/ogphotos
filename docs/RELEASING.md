# Releasing OGphotos

Releases are built by `.github/workflows/release.yml`. A `vX.Y.Z` tag builds the Windows
installer, the macOS dmg (arm64) and the Linux tarball (x86_64 + aarch64), installs and
starts each one on a clean runner (smoke test: `/healthz` must answer as `ogphotos`, and the
installed Python must download one pinned model file through the app's own download code), then
creates a **draft** GitHub release with the files, `SHA256SUMS.txt` and the changelog
section as the notes. Nothing is published until you publish the draft.

## Dry run

Before tagging, check the build on the branch: **Actions → release → Run workflow**. This
runs the version check, the three builds and the three smoke tests, and attaches the
packages to the run as artifacts. It never creates a release.

## Steps

1. **Bump the version** (both must match; the workflow fails otherwise):
   - `pyproject.toml` → `version = "X.Y.Z"`
   - `app/__init__.py` → `__version__ = "X.Y.Z"`
2. **Update `CHANGELOG.md`**: add a `## [X.Y.Z] - YYYY-MM-DD` section. Its text becomes the
   release notes; the release job fails if the section is missing or empty.
3. **Refresh the lockfile**: `uv lock` (CI runs `uv lock --check`).
4. **Commit**: `git commit -am "release: vX.Y.Z"` and get CI green on `main`.
5. **Tag and push**:
   ```bash
   git tag vX.Y.Z && git push --tags
   ```
6. **Review the draft** on the GitHub Releases page once the workflow finishes:
   - download each artifact and check it against `SHA256SUMS.txt`;
   - Windows: run `OGphotos-Setup-X.Y.Z.exe`, launch, import a few photos, uninstall;
   - macOS: open `OGphotos-X.Y.Z.dmg`, drag to Applications, launch (right-click → Open
     the first time; the app is unsigned);
   - macOS Gatekeeper check (CI can't do this; downloads made by a browser are
     quarantined, CI's aren't): on a Mac, download the dmg **with a browser** from the
     draft, drag the app to Applications, open it via right-click → Open, and confirm
     Terminal starts and first-run setup begins;
   - Linux: `tar -xzf ogphotos-X.Y.Z-linux.tar.gz && ./ogphotos/launch.sh`.
7. **Publish** the draft.

## If something fails

- **version** job: the tag, `pyproject.toml`, `app/__init__.py` or the `CHANGELOG.md`
  heading disagree. Fix, delete the tag (`git tag -d vX.Y.Z && git push --delete origin
  vX.Y.Z`), commit and tag again.
- **smoke** job: download the `smoke-log-<os>` artifact (the app log and launcher output).
- A broken draft can simply be deleted on the Releases page and the tag re-pushed.
