# -*- coding: utf-8 -*-
# Telegram Job Bot (PTB 22.x)
# Старт/стоп: ./run.sh  (остановка — Ctrl+C)

from __future__ import annotations

import os
import re
import csv
import io
import logging
import asyncio
from typing import Any, List, Dict, Optional, Tuple

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

# ================== НАСТРОЙКИ ==================
TOKEN = "8449257401:AAFLCuuyBi1Mmd63gkF6ujB1hGSdAFyn_9w"

# Источник данных: экспорт листа Google Sheets в CSV
SHEET_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1_KIjSrpBbc3xv-fuobapE2xj12kR6-tUmZEiLe41NKw/export?format=csv"
)

# Индексы столбцов (0-based)
COL_ROLE = 6           # G — Вид деятельности
COL_CITY = 7           # H — Город
COL_TITLE = 2          # C — Название/Компания
COL_DESC = 3           # D — Описание
COL_SAL_PRIOR = 11     # L — Зарплата (приоритет)
COL_SAL_FALLBACK = 10  # K — Зарплата (если L пустой)

# Выбор ролей
ROLES = ["Водитель", "Курьер", "Разнорабочий", "Работник торгового зала"]

# Сообщения
MSG_GREETING = "Привет! Я помогу найти вакансии с самыми высокими зарплатами."
MSG_CHOOSE_CITY = "Выберите город:"
MSG_CHOOSE_ROLE = "Кем хотите работать?"
MSG_ASK_SALARY = "Укажите желаемую зарплату в месяц (например: 90 000):"
MSG_SALARY_BAD = "Не понял. Напишите только цифрами (можно с пробелами), например: 90000"
MSG_FOUND_ABOVE = "🎉 Ура, я нашёл вакансии с зарплатой выше или равной желаемой!"
MSG_FOUND_BELOW = "🙇 К сожалению, вакансий с такой зарплатой нет, но вот лучшие близкие варианты:"
MSG_EMPTY = "По выбранным параметрам ничего не нашли. Попробуйте изменить город или сумму."

# Логи тише
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
for noisy in ("httpx", "httpcore", "telegram", "telegram.ext", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("bot")


# ================== УТИЛИТЫ ==================
def chunked(items: List[Any], n: int) -> List[List[Any]]:
    return [items[i:i+n] for i in range(0, len(items), n)]

def kb(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows or [])

def norm(s: Optional[str]) -> str:
    return (s or "").strip()

def norm_lc(s: Optional[str]) -> str:
    return norm(s).lower()

def parse_salary_value(text: str) -> Optional[int]:
    """Извлекает число (руб/мес) из произвольной строки: '90 000', '90к/90k', '100 тыс', '100-120'."""
    if not text:
        return None
    t = str(text).lower()
    t = (t.replace("рублей", "руб").replace("руб.", "руб").replace("₽", "руб")
           .replace("тыс.", "тыс").replace("тысяч", "тыс").replace("тысячи", "тыс")
           .replace("k", "к"))
    # диапазон
    m = re.search(r'(\d[\d\s.,]*)\s*(?:-|–|—)\s*(\d[\d\s.,]*)\s*(к|тыс)?', t)
    if m:
        a, b, suf = m.group(1), m.group(2), m.group(3)
        return max(_to_int(a, suf), _to_int(b, suf)) or None
    # обычное число + возможный суффикс
    m = re.search(r'(\d[\d\s.,]*)\s*(к|тыс)?', t)
    if m:
        return _to_int(m.group(1), m.group(2)) or None
    return None

def _to_int(raw: str, suf: Optional[str]) -> int:
    digits = re.sub(r'\D', '', raw or "")
    if not digits:
        return 0
    n = int(digits)
    if suf in ("к", "тыс"):
        n *= 1000
    return n

def clean_description(raw: str) -> str:
    """Чистим HTML, лишние символы, маркеры и добавляем эмодзи к ключевым блокам."""
    if not raw:
        return "Описание отсутствует."
    t = raw
    t = re.sub(r'<br\s*/?>', '\n', t, flags=re.I)
    t = re.sub(r'<[^>]+>', '', t)
    t = t.replace("&nbsp;", " ")
    # убрать рекламные/служебные строки типа «Ищем Водителей...»
    t = re.sub(r'(?im)^\s*ищем\s+[^.\n]+\s*$', '', t)
    # маркеры → точки
    t = t.replace("—", "-").replace("–", "-")
    t = re.sub(r'[\u2022•▪︎▫︎●◦◆►✔✅➤➔➤]', '•', t)
    t = re.sub(r'-{2,}', '-', t)
    # заголовки
    repl = [
        (r'(?im)^\s*мы предлагаем\s*:?$', "✨ Мы предлагаем:"),
        (r'(?im)^\s*мы ожидаем[^:]*\s*:?$', "🧩 Мы ожидаем:"),
        (r'(?im)^\s*требования\s*:?$', "📌 Требования:"),
        (r'(?im)^\s*обязанности\s*:?$', "🛠 Обязанности:"),
        (r'(?im)^\s*условия\s*:?$', "📄 Условия:")
    ]
    for pat, rep in repl:
        t = re.sub(pat, rep, t)
    t = re.sub(r'\n{3,}', '\n\n', t).strip()
    return t or "Описание отсутствует."

def salary_from_row(row: List[str]) -> int:
    val = ""
    if len(row) > COL_SAL_PRIOR and norm(row[COL_SAL_PRIOR]):
        val = row[COL_SAL_PRIOR]
    elif len(row) > COL_SAL_FALLBACK and norm(row[COL_SAL_FALLBACK]):
        val = row[COL_SAL_FALLBACK]
    s = parse_salary_value(val)
    return s or 0

def title_from_row(row: List[str]) -> str:
    parts = []
    if len(row) > COL_TITLE and norm(row[COL_TITLE]):
        parts.append(norm(row[COL_TITLE]))
    if len(row) > COL_ROLE and norm(row[COL_ROLE]):
        parts.append(norm(row[COL_ROLE]))
    return " — ".join(parts) if parts else "Вакансия"

def city_from_row(row: List[str]) -> str:
    return norm(row[COL_CITY]) if len(row) > COL_CITY else ""

def role_match(row: List[str], role: str) -> bool:
    return role.lower() in norm_lc(row[COL_ROLE] if len(row) > COL_ROLE else "")


# ================== ДАННЫЕ ==================
def fetch_sheet_rows_sync() -> List[List[str]]:
    with httpx.Client(timeout=20) as client:
        r = client.get(SHEET_CSV_URL, follow_redirects=True)
        r.raise_for_status()
        text = r.content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = [list(row) for row in reader]
    # срежем хедер, если там "вид деятельности"
    if rows and len(rows[0]) > COL_ROLE and "вид" in norm_lc(rows[0][COL_ROLE]):
        rows = rows[1:]
    return rows

async def get_rows(context: ContextTypes.DEFAULT_TYPE) -> List[List[str]]:
    cache = context.bot_data.get("rows_cache")
    if isinstance(cache, list) and cache:
        return cache
    rows = await asyncio.to_thread(fetch_sheet_rows_sync)
    context.bot_data["rows_cache"] = rows
    return rows

def unique_cities(rows: List[List[str]]) -> List[str]:
    seen, out = set(), []
    for r in rows:
        c = city_from_row(r)
        if not c:
            continue
        key = c.lower()
        if key not in seen:
            seen.add(key)
            out.append(c)
    if "Москва" in out:
        out = ["Москва"] + [x for x in out if x != "Москва"]
    return out or ["Москва"]


# ================== ХЕНДЛЕРЫ ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    buttons = [[InlineKeyboardButton("🔎 Найти вакансии", callback_data="find")]]
    await update.effective_message.reply_text(MSG_GREETING, reply_markup=kb(buttons))

async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = (q.data or "") if q else ""
    if q: await q.answer()

    if data == "find":
        rows = await get_rows(context)
        cities = unique_cities(rows)
        # пока основная — Москва
        btns = [InlineKeyboardButton(c, callback_data=f"city:{c}") for c in cities]
        buttons = chunked(btns, 2)
        await (q.edit_message_text if q else update.effective_message.reply_text)(
            MSG_CHOOSE_CITY, reply_markup=kb(buttons)
        )
        return

    if data.startswith("city:"):
        city = data.split(":", 1)[1]
        context.user_data["city"] = city
        btns = [InlineKeyboardButton(r, callback_data=f"role:{r}") for r in ROLES]
        buttons = chunked(btns, 2)
        await (q.edit_message_text if q else update.effective_message.reply_text)(
            MSG_CHOOSE_ROLE, reply_markup=kb(buttons)
        )
        return

    if data.startswith("role:"):
        role = data.split(":", 1)[1]
        context.user_data["role"] = role
        context.user_data["await_salary"] = True
        await (q.edit_message_text if q else update.effective_message.reply_text)(
            f"Город: {context.user_data.get('city','Москва')}\nРоль: {role}\n\n{MSG_ASK_SALARY}"
        )
        return

    if data.startswith("vac:"):
        idx = int(data.split(":", 1)[1])
        results = context.user_data.get("results", [])
        if not (0 <= idx < len(results)):
            if q: await q.answer("Вакансия не найдена", show_alert=True)
            return
        row = results[idx]["row"]
        desc = clean_description(row[COL_DESC] if len(row) > COL_DESC else "")
        title = title_from_row(row)
        city = city_from_row(row)
        sal = results[idx]["salary"]
        buttons = [
            [InlineKeyboardButton("◀️ Назад к вакансиям", callback_data="back_to_results"),
             InlineKeyboardButton("✅ Откликнуться", callback_data=f"apply:{idx}")]
        ]
        text = f"{title}\n🏙 {city}\n💰 Зарплата: {sal:,} ₽\n\n{desc}".replace(",", " ")
        await (q.edit_message_text if q else update.effective_message.reply_text)(
            text, reply_markup=kb(buttons)
        )
        return

    if data == "back_to_results":
        await show_results_list(update, context)
        return

    if data.startswith("apply:"):
        if q: await q.answer("Заявка отправлена! (демо)", show_alert=True)
        return

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_salary"):
        return
    want = parse_salary_value(update.effective_message.text or "")
    if not want:
        await update.effective_message.reply_text(MSG_SALARY_BAD)
        return
    context.user_data["await_salary"] = False
    context.user_data["want_salary"] = want
    await do_search(update, context)

async def do_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = context.user_data.get("city", "Москва")
    role = context.user_data.get("role", "")
    want = context.user_data.get("want_salary", 0)

    rows_all = await get_rows(context)
    # фильтр по городу
    rows = [r for r in rows_all if norm_lc(r[COL_CITY] if len(r) > COL_CITY else "") == norm_lc(city)]
    # фильтр по роли
    rows = [r for r in rows if (len(r) > COL_ROLE and role_match(r, role))]
    if not rows:
        await update.effective_message.reply_text(MSG_EMPTY)
        return

    prepared = [{"row": r, "salary": salary_from_row(r)} for r in rows]
    prepared.sort(key=lambda x: x["salary"], reverse=True)
    top = prepared[:5]
    context.user_data["results"] = top

    any_ge = any(item["salary"] >= want for item in top)
    head = MSG_FOUND_ABOVE if any_ge else MSG_FOUND_BELOW

    lines = []
    for i, it in enumerate(top, 1):
        row = it["row"]
        sal = it["salary"]
        lines.append(f"{i}. {title_from_row(row)} — {sal:,} ₽".replace(",", " "))
    text = f"{head}\n\nГород: {city}\nРоль: {role}\nЖелаемая: {want:,} ₽\n\n".replace(",", " ") + "\n".join(lines)

    # кнопки 1..N
    btns = [[InlineKeyboardButton(f"{i+1}", callback_data=f"vac:{i}") for i in range(len(top))]]
    btns.append([InlineKeyboardButton("⬅️ Назад (выбрать заново)", callback_data="find")])

    await update.effective_message.reply_text(text, reply_markup=kb(btns))

async def show_results_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = context.user_data.get("results", [])
    if not results:
        await update.effective_message.reply_text(MSG_EMPTY)
        return
    lines = []
    for i, it in enumerate(results, 1):
        row = it["row"]
        sal = it["salary"]
        lines.append(f"{i}. {title_from_row(row)} — {sal:,} ₽".replace(",", " "))
    btns = [[InlineKeyboardButton(f"{i+1}", callback_data=f"vac:{i}") for i in range(len(results))]]
    btns.append([InlineKeyboardButton("⬅️ Назад (выбрать заново)", callback_data="find")])
    await update.effective_message.reply_text("Выберите вакансию:\n\n" + "\n".join(lines), reply_markup=kb(btns))


# ================== WIRING ==================
def build_app() -> Application:
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app

def main() -> None:
    app = build_app()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
