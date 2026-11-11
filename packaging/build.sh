#!/usr/bin/env bash
# Construit l'exécutable via PyInstaller (sortie : dist/).
set -euo pipefail
cd "$(dirname "$0")/.."
pyinstaller --noconfirm --windowed --name AssetsTracker \
  --paths src \
  --add-data "src/assets_tracker/db/schema.sql:assets_tracker/db" \
  --add-data "src/assets_tracker/assets:assets_tracker/assets" \
  --icon "packaging/assets-tracker.icns" \
  --collect-all sqlcipher3 \
  --hidden-import LocalAuthentication \
  --hidden-import Security \
  src/assets_tracker/__main__.py
