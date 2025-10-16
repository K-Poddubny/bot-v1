#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# venv
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
source .venv/bin/activate

python3 -m pip -q install --upgrade pip
python3 -m pip -q install -r requirements.txt

echo "[run] starting bot … (Ctrl+C to stop)"
python3 bot.py
