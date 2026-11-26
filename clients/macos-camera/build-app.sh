#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="VisionSentinelCamera"
APP_BUNDLE="$ROOT/dist/${APP_NAME}.app"

cd "$ROOT"
echo "Building ${APP_NAME} (release)..."
swift build -c release

BIN_PATH="$(swift build -c release --show-bin-path)/${APP_NAME}"
if [[ ! -x "$BIN_PATH" ]]; then
  echo "Expected binary not found at $BIN_PATH" >&2
  exit 1
fi

rm -rf "$APP_BUNDLE"
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"
cp "$BIN_PATH" "$APP_BUNDLE/Contents/MacOS/${APP_NAME}"
cp "$ROOT/Info.plist" "$APP_BUNDLE/Contents/Info.plist"
if [[ -f "$ROOT/Resources/AppIcon.icns" ]]; then
  cp "$ROOT/Resources/AppIcon.icns" "$APP_BUNDLE/Contents/Resources/AppIcon.icns"
fi

# Ad-hoc sign so macOS Camera & Microphone TCC permissions bind cleanly to the app bundle.
codesign --force --deep --sign - "$APP_BUNDLE"

echo ""
echo "Built: $APP_BUNDLE"
echo "Launch with:"
echo "  open \"$APP_BUNDLE\""
