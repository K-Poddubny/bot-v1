#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PY="/usr/bin/python3"
[ -d ".venv" ] || "$PY" -m venv .venv
source .venv/bin/activate

python -m pip -q install --upgrade pip >/dev/null 2>&1 || true
pip -q install -r requirements.txt >/dev/null 2>&1 || true

# Уберём вебхук перед стартом (ещё до инициализации PTB)
TOKEN=$(grep -Eo 'TOKEN = os\.getenv\("TELEGRAM_BOT_TOKEN"\) or "[^"]+' bot.py | sed -E 's/.* or "([^"]+)"/\1/')
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN:-$TOKEN}/deleteWebhook" >/dev/null || true

exec python bot.py
