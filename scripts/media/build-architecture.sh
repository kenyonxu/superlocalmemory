#!/bin/sh
# Rebuild docs/assets/slm-architecture.png from the hand-drawn slm-architecture.svg.
# Needs Google Chrome (headless) and ImageMagick.
set -e
cd "$(dirname "$0")/../.."
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
TMP="$(mktemp -d)"
"$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \
  --window-size=1600,1080 --screenshot="$TMP/arch.png" "file://$PWD/docs/assets/slm-architecture.svg"
magick "$TMP/arch.png" -strip -define png:compression-level=9 docs/assets/slm-architecture.png
rm -rf "$TMP"
