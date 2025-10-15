#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PY="/usr/bin/python3"
[ -d ".venv" ] || "$PY" -m venv .venv
source .venv/bin/activate

python -m pip -q install --upgrade pip >/dev/null 2>&1 || true
pip -q install -r requirements.txt >/dev/null 2>&1 || true

# Достаём токен: из ENV или из fallback в bot.py
BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-$(grep -Eo 'TOKEN = os\.getenv\("TELEGRAM_BOT_TOKEN"\) or "[^"]+' bot.py | sed -E 's/.* or "([^"]+)"/\1/')}"
if [ -z "${BOT_TOKEN:-}" ]; then
  echo "[!] Не смогли прочитать токен."
  exit 1
fi

# На всякий случай уберём вебхук, чтобы polling не конфликтовал
curl -s "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook" >/dev/null || true

# Запускаем бота в переднем плане
exec python bot.py
