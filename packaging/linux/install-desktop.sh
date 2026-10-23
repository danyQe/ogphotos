#!/bin/sh
# Add OGphotos to the applications menu (and the desktop, if there is one). Safe to re-run.
# Lives next to launch.sh in the unpacked folder; paths in the entry point at that folder.
set -eu

DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
TEMPLATE="$DIR/ogphotos.desktop.in"
[ -f "$TEMPLATE" ] || { echo "install-desktop: $TEMPLATE not found" >&2; exit 1; }

# Escape \ & and | so the folder name is literal in the sed replacement.
ESCAPED=$(printf '%s' "$DIR" | sed 's/[\\&|]/\\&/g')
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
sed "s|@DIR@|$ESCAPED|g" "$TEMPLATE" >"$APPS/ogphotos.desktop"
chmod +x "$APPS/ogphotos.desktop"
echo "Added $APPS/ogphotos.desktop"

if command -v xdg-user-dir >/dev/null 2>&1; then
    DESKTOP=$(xdg-user-dir DESKTOP 2>/dev/null || true)
    if [ -n "$DESKTOP" ] && [ -d "$DESKTOP" ]; then
        cp "$APPS/ogphotos.desktop" "$DESKTOP/ogphotos.desktop" 2>/dev/null || true
        chmod +x "$DESKTOP/ogphotos.desktop" 2>/dev/null || true
        gio set "$DESKTOP/ogphotos.desktop" metadata::trusted true 2>/dev/null || true
    fi
fi
