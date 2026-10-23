#!/bin/sh
# Build dist/ogphotos-VERSION-linux.tar.gz. Usage: packaging/linux/build_tar.sh VERSION
set -eu

VERSION=${1:?usage: build_tar.sh VERSION}
HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
ROOT=$(CDPATH='' cd -- "$HERE/../.." && pwd -P)
DIST="$ROOT/dist"
OUT="$DIST/linux-build"

rm -rf "$OUT"
mkdir -p "$OUT" "$DIST"
(cd "$ROOT" && uv run python packaging/stage.py --target linux --out "$OUT")

cp "$HERE/ogphotos.desktop.in" "$HERE/install-desktop.sh" "$OUT/ogphotos/"
chmod +x "$OUT/ogphotos/install-desktop.sh" "$OUT/ogphotos/launch.sh"

tar -czf "$DIST/ogphotos-$VERSION-linux.tar.gz" -C "$OUT" ogphotos
echo "$DIST/ogphotos-$VERSION-linux.tar.gz"
