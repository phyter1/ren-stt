#!/bin/bash
set -euo pipefail

# Build RenSTT.app and package it into a DMG for drag-to-install.
#
# Usage:
#   ./build-dmg.sh              # builds RenSTT.dmg in dist/
#   ./build-dmg.sh --app-only   # just build the .app, no DMG

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$REPO_DIR/dist"
APP_DIR="$BUILD_DIR/RenSTT.app"
CONTENTS="$APP_DIR/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"

GREEN='\033[0;32m'
NC='\033[0m'
info() { echo -e "${GREEN}▸${NC} $1"; }

APP_ONLY=false
[[ "${1:-}" == "--app-only" ]] && APP_ONLY=true

# ── Clean ───────────────────────────────────────────────────────────

rm -rf "$APP_DIR"
mkdir -p "$MACOS" "$RESOURCES"

# ── Assemble .app ───────────────────────────────────────────────────

info "Building RenSTT.app..."

# Info.plist
cp "$REPO_DIR/app/Info.plist" "$CONTENTS/Info.plist"

# Executable launcher
cp "$REPO_DIR/app/launcher.sh" "$MACOS/ren-stt"
chmod +x "$MACOS/ren-stt"

# Python sources (client + server)
cp "$REPO_DIR/stt-cli.py" "$RESOURCES/"
cp "$REPO_DIR/stt-indicator.py" "$RESOURCES/"
cp "$REPO_DIR/stt-menubar.py" "$RESOURCES/"
cp "$REPO_DIR/stt-server.py" "$RESOURCES/"
cp "$REPO_DIR/config.py" "$RESOURCES/"
cp "$REPO_DIR/requirements-client.txt" "$RESOURCES/"
cp "$REPO_DIR/requirements-server.txt" "$RESOURCES/"

info "RenSTT.app built at $APP_DIR"

if $APP_ONLY; then
    echo ""
    info "Done (app only). To install: cp -r $APP_DIR /Applications/"
    exit 0
fi

# ── Build DMG ───────────────────────────────────────────────────────

info "Creating DMG..."

DMG_NAME="RenSTT"
DMG_PATH="$BUILD_DIR/$DMG_NAME.dmg"
DMG_TEMP="$BUILD_DIR/dmg-staging"

# Clean up old artifacts
rm -rf "$DMG_TEMP" "$DMG_PATH"
mkdir -p "$DMG_TEMP"

# Stage the .app and Applications symlink
cp -r "$APP_DIR" "$DMG_TEMP/"
ln -s /Applications "$DMG_TEMP/Applications"

# Create DMG
hdiutil create \
    -volname "$DMG_NAME" \
    -srcfolder "$DMG_TEMP" \
    -ov \
    -format UDZO \
    "$DMG_PATH" \
    > /dev/null

rm -rf "$DMG_TEMP"

info "DMG created at $DMG_PATH"
echo ""
info "To install: open $DMG_PATH and drag RenSTT to Applications"
echo ""
ls -lh "$DMG_PATH"
