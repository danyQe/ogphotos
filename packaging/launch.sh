#!/bin/sh
# OGphotos launcher for macOS and Linux (POSIX sh; the macOS app runs it in Terminal).
#
# The download ships the app source, uv.lock and a pinned uv binary. The first launch (and
# the first after an upgrade changes uv.lock) builds the Python environment with
# `uv sync --frozen` (app + one runtime group, no dev tools) in the per-user app folder, and
# again if the runtime group changes (e.g. an NVIDIA GPU appears); later launches skip uv entirely
# and start the environment's Python directly, so they make no network calls.
#
# Two double-clicks during the first-run sync are safe without a lock here: uv holds its
# own lock on the environment, and app.launcher takes a single-instance lock once started.

set -u

HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) || exit 1

case "$(uname -s)" in
    Darwin)
        OS=macos
        DEFAULT_HOME="$HOME/Library/Application Support/OGphotos"
        ;;
    *)
        OS=linux
        DEFAULT_HOME="$HOME/.local/share/ogphotos"
        ;;
esac
HOME_DIR=${OGPHOTOS_HOME:-$DEFAULT_HOME}
export OGPHOTOS_HOME="$HOME_DIR"

wait_for_key() {
    if [ "${OGPHOTOS_NONINTERACTIVE:-}" != "1" ]; then
        printf 'Press Enter to close.'
        read -r _ || true
    fi
}

fail() {
    printf '%s\n' "$1" >&2
    wait_for_key
    exit 1
}

# After the user approves the app once, clear quarantine so the bundled uv isn't blocked.
if [ "$OS" = "macos" ]; then
    xattr -dr com.apple.quarantine "$HERE" 2>/dev/null || true
fi

MACHINE=$(uname -m)
case "$MACHINE" in
    arm64 | aarch64) ARCH=aarch64 ;;
    x86_64 | amd64) ARCH=x86_64 ;;
    *) ARCH=$MACHINE ;;
esac
UV="$HERE/bin/$ARCH/uv"
if [ ! -x "$UV" ]; then
    fail "OGphotos: unsupported CPU ($MACHINE). This download supports: $(ls "$HERE/bin" 2>/dev/null | tr '\n' ' ')"
fi

export UV_PYTHON_INSTALL_DIR="$HOME_DIR/python"
export UV_PROJECT_ENVIRONMENT="$HOME_DIR/env"
export UV_CACHE_DIR="$HOME_DIR/cache"
export UV_PYTHON_PREFERENCE=only-managed
export UV_PYTHON=3.12
export PYTHONDONTWRITEBYTECODE=1
# The project is virtual (not installed into the env), so Python finds `app` here.
export PYTHONPATH="$HERE"

PY="$HOME_DIR/env/bin/python"
MARKER="$HOME_DIR/env/.lock-hash"

lock_hash() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$HERE/uv.lock"
    else
        shasum -a 256 "$HERE/uv.lock"
    fi | cut -d ' ' -f 1
}

# Runtime group from pyproject.toml: `cuda` (CUDA 13 torch + onnxruntime-gpu) needs an
# NVIDIA driver 580+ and compute capability 7.5+; anything else gets `cpu`. The app uses
# the GPU when it can and falls back to the CPU either way. OGPHOTOS_TORCH=cpu|cuda overrides.
torch_group() {
    case "${OGPHOTOS_TORCH:-}" in
        cpu | cuda) echo "$OGPHOTOS_TORCH"; return ;;
    esac
    if [ "$OS" != "linux" ] || ! command -v nvidia-smi >/dev/null 2>&1; then
        echo cpu; return
    fi
    DRV=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null \
        | head -n 1 | cut -d . -f 1 | tr -d ' ')
    CC=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null \
        | head -n 1 | tr -d ' .')
    case "$DRV" in '' | *[!0-9]*) echo cpu; return ;; esac
    case "$CC" in '' | *[!0-9]*) echo cpu; return ;; esac
    if [ "$DRV" -ge 580 ] && [ "$CC" -ge 75 ]; then echo cuda; else echo cpu; fi
}
GROUP=$(torch_group)

HASH=$(lock_hash 2>/dev/null) || HASH=""
OLD=""
if [ -f "$MARKER" ]; then
    OLD=$(cat "$MARKER" 2>/dev/null) || OLD=""
fi

if [ -z "$HASH" ] || [ "$HASH $GROUP" != "$OLD" ] || [ ! -x "$PY" ]; then
    if [ "$GROUP" = "cuda" ]; then
        echo "Setting up OGphotos for your NVIDIA GPU (one-time download of about 4 GB; usually 10–60 min)…"
    else
        echo "Setting up OGphotos (one-time download of about 1 GB; usually 5–30 min)…"
    fi
    mkdir -p "$HOME_DIR" || fail "OGphotos: can't create $HOME_DIR"
    if ! "$UV" sync --frozen --no-default-groups --group "$GROUP" --project "$HERE"; then
        fail "OGphotos setup failed. Please check your internet connection and re-run OGphotos."
    fi
    if [ -n "$HASH" ]; then
        printf '%s\n' "$HASH $GROUP" >"$MARKER"
    fi
fi

# "python -m" puts the current folder first on sys.path; run from here so an unrelated
# "app" folder in the caller's directory can't shadow ours.
cd "$HERE" || fail "OGphotos: can't enter $HERE"

# exec replaces this script, so a broken environment is caught here, while we can still
# explain it, instead of the window closing silently.
if ! "$PY" -c "" >/dev/null 2>&1; then
    fail "OGphotos couldn't start. Delete the folder $HOME_DIR/env and run again to repair."
fi

# Linux: once, offer to add OGphotos to the applications menu and desktop. Only when the
# entry is missing, we're on a terminal, and the user hasn't been asked before.
DESKTOP_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/applications/ogphotos.desktop"
if [ "$OS" = "linux" ] && [ ! -f "$DESKTOP_FILE" ] && [ ! -f "$HOME_DIR/.desktop-asked" ] \
    && [ -t 0 ] && [ -z "${OGPHOTOS_NONINTERACTIVE:-}" ] && [ -f "$HERE/install-desktop.sh" ]; then
    printf 'Add OGphotos to your applications menu and desktop? [Y/n] '
    ANSWER=""
    read -r ANSWER || true
    mkdir -p "$HOME_DIR" && : >"$HOME_DIR/.desktop-asked"
    case "$ANSWER" in
        [Nn]*) ;;
        *) sh "$HERE/install-desktop.sh" || true ;;
    esac
fi

exec "$PY" -m app.launcher "$@"
