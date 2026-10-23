#!/bin/sh
# Build dist/OGphotos-VERSION.dmg (macOS arm64). Usage: packaging/macos/build_app.sh VERSION
# Run from anywhere on a Mac with uv installed.
set -eu

VERSION=${1:?usage: build_app.sh VERSION}
HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
ROOT=$(CDPATH='' cd -- "$HERE/../.." && pwd -P)
DIST="$ROOT/dist"
WORK="$DIST/macos-build"

rm -rf "$WORK"
mkdir -p "$WORK/stage" "$DIST"

(cd "$ROOT" && uv run python packaging/stage.py --target macos --out "$WORK/stage")

APP="$WORK/dmg/OGphotos.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
sed "s/@VERSION@/$VERSION/g" "$HERE/Info.plist" >"$APP/Contents/Info.plist"
cp "$HERE/OGphotos" "$APP/Contents/MacOS/OGphotos"
cp "$WORK/stage/ogphotos/icons/ogphotos.icns" "$APP/Contents/Resources/ogphotos.icns"
cp -R "$WORK/stage/ogphotos" "$APP/Contents/Resources/ogphotos"
chmod +x "$APP/Contents/MacOS/OGphotos" "$APP/Contents/Resources/ogphotos/launch.sh"
# Ad-hoc signature (no Apple ID): arm64 needs a valid signature to run at all.
if command -v codesign >/dev/null 2>&1; then
    codesign --force --deep -s - "$APP"
else
    echo "codesign not found; leaving OGphotos.app unsigned" >&2
fi
ln -s /Applications "$WORK/dmg/Applications"

hdiutil create -volname OGphotos -srcfolder "$WORK/dmg" -ov -format UDZO "$DIST/OGphotos-$VERSION.dmg"
echo "$DIST/OGphotos-$VERSION.dmg"
