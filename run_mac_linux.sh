#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

APP_HOME="${XDG_CACHE_HOME:-$HOME/.cache}/stock-analyser"
VENV_DIR="$APP_HOME/venv"
HASH_FILE="$APP_HOME/requirements.sha256"
mkdir -p "$APP_HOME"

NEW_ENV=0
if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "[Stock Analyser] First-time Python environment setup..."
  python3 -m venv "$VENV_DIR"
  NEW_ENV=1
fi

source "$VENV_DIR/bin/activate"
if command -v sha256sum >/dev/null 2>&1; then
  REQ_HASH="$(sha256sum requirements.txt | awk '{print $1}')"
else
  REQ_HASH="$(shasum -a 256 requirements.txt | awk '{print $1}')"
fi
OLD_HASH=""
[ -f "$HASH_FILE" ] && OLD_HASH="$(cat "$HASH_FILE")"

if [ "$NEW_ENV" = "1" ] || [ "$REQ_HASH" != "$OLD_HASH" ]; then
  echo "[Stock Analyser] Dependencies are new or changed. Installing once..."
  python -m pip install --disable-pip-version-check -r requirements.txt
  printf '%s\n' "$REQ_HASH" > "$HASH_FILE"
  echo "[Stock Analyser] Dependency environment is ready."
fi

echo "[Stock Analyser] Starting app using cached environment..."
python -m streamlit run app.py
