#!/usr/bin/env bash
# Build the standalone macOS .app bundle for the Card CSV Transformer.
#
# IMPORTANT: PyInstaller cannot cross-compile. You must run this script
# on a Mac to produce a Mac binary; running it on Windows or Linux will
# produce a binary for that platform instead.
#
# Prerequisites (one-time setup on the build machine):
#     python3 -m pip install pyinstaller tkinterdnd2
#
# After this script finishes, hand `dist/CardCSVTransformer.app` (macOS)
# or `dist/CardCSVTransformer` (Linux) to the recipient.
#
# macOS notes for the recipient:
#   - The .app is unsigned. On first launch macOS will block it with
#     "cannot be opened because the developer cannot be verified".
#     Right-click the app and choose Open, then confirm — or run:
#         xattr -dr com.apple.quarantine /path/to/CardCSVTransformer.app
#   - For a properly signed/notarized build you need an Apple Developer
#     ID certificate; pass it via --codesign-identity below.

set -euo pipefail

echo "Building CardCSVTransformer ..."

python3 -m PyInstaller \
  --onefile \
  --windowed \
  --noconfirm \
  --name CardCSVTransformer \
  --add-data "parallel_vocab.txt:." \
  --collect-all tkinterdnd2 \
  app.py

echo
if [[ "$(uname -s)" == "Darwin" ]]; then
    echo "Done. Output: dist/CardCSVTransformer.app"
else
    echo "Done. Output: dist/CardCSVTransformer"
fi
