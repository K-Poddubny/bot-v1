#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PY="/usr/bin/python3"

# venv, зависимости — без лишней болтовни
[ -d ".venv" ] || "$PY" -m venv .venv
source .venv/bin/activate
python -m pip -q install --upgrade pip >/dev/null 2>&1 || true
pip -q install -r requirements.txt >/dev/null 2>&1 || true

# Запуск в ПЕРЕДНЕМ плане — останавливай Ctrl+C
exec python bot.py
