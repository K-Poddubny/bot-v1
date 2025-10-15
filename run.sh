#!/usr/bin/env bash
set -euo pipefail
set -x
cd "$(dirname "$0")"

PY="/usr/bin/python3"
[ -d ".venv" ] || "$PY" -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt

# Токен: ENV или fallback из bot.py
BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-$(grep -Eo 'TOKEN = os\.getenv\("TELEGRAM_BOT_TOKEN"\) or "[^"]+' bot.py | sed -E 's/.* or "([^"]+)"/\1/')}"
echo "[i] Using token tail: ...${BOT_TOKEN: -8}"

# На всякий случай снимаем вебхук (чтобы polling не конфликтовал)
curl -sS "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook"

# Запуск без буферизации вывода (чтобы видеть трейсбек/строку online)
export PYTHONUNBUFFERED=1
exec python -u bot.py
