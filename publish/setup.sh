#!/usr/bin/env bash
# One-time environment setup for building the book. Run from anywhere.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v brew >/dev/null; then
  echo "Homebrew is required (https://brew.sh)"; exit 1
fi
# WeasyPrint needs pango/cairo; epubcheck validates the EPUB (needs Java, pulled in by brew).
brew list pango >/dev/null 2>&1 || brew install pango
brew list epubcheck >/dev/null 2>&1 || brew install epubcheck

[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -q -r publish/requirements.txt

# Fonts: downloads the official Noto releases and merges Myanmar + Latin faces (network needed once).
if [ ! -f publish/fonts/NotoSansMyanmar-Regular.ttf ]; then
  python3 publish/fonts.py
fi
echo "Setup complete. Activate with: source .venv/bin/activate   then build with: python3 publish/build.py all"
