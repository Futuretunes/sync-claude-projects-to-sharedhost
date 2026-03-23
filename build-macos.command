#!/bin/zsh
set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

.venv/bin/pip install -e .
.venv/bin/pyinstaller \
  --name "Sync to Web" \
  --windowed \
  --noconfirm \
  --paths src \
  src/sync_to_web/__main__.py
