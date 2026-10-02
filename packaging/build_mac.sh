#!/bin/bash
# Build "LULC Fetch.app" and "LULC-Fetch.dmg" in dist/ (run from the project folder, with the .venv set up).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=$([ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)
[ -f "packaging/LULC Fetch.icns" ] || $PY packaging/make_icon.py "packaging/LULC Fetch.icns"
rm -rf build "dist/LULC Fetch" "dist/LULC Fetch.app" dist/LULC-Fetch.dmg
$PY -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging/lulc_fetch.spec
rm -rf "dist/LULC Fetch"   # intermediate folder; the .app holds everything
# disk image with the app and a shortcut to Applications (drag to install)
STAGE=$(mktemp -d)
cp -R "dist/LULC Fetch.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "LULC Fetch" -srcfolder "$STAGE" -ov -format UDZO dist/LULC-Fetch.dmg >/dev/null
rm -rf "$STAGE"
echo "Built: dist/LULC Fetch.app and dist/LULC-Fetch.dmg"
