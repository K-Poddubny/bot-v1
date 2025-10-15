##EARLY_WARN_MUTE##
import warnings
try:
    from urllib3.exceptions import NotOpenSSLWarning
    warnings.filterwarnings('ignore', category=NotOpenSSLWarning)
except Exception:
    pass
import os, re, csv, io, asyncio, logging
from typing import List, Dict, Any, Optional, Optional

from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN not set")

import requests
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, ContextTypes, ConversationHandler,
    CallbackQueryHandler, MessageHandler, filters
)

import warnings
from urllib3.exceptions import NotOpenSSLWarning
warnings.filterwarnings('ignore', category=NotOpenSSLWarning)
logging.basicConfig(level=logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)
logging.getLogger('telegram.ext').setLevel(logging.WARNING)
logger = logging.getLogger("jobbot")

# ---- Константы сценария
CITY_CHOOSER, ROLE_CHOOSER, SALARY_ASK, SHOW_RESULTS = range(4)

CITIES = ["Москва"]  # пополняемый список
ROLES = ["Водитель", "Курьер", "Разнорабочий", "Работник торгового зала"]

SHEET_ID = "1_KIjSrpBbc3xv-fuobapE2xj12kR6-tUmZEiLe41NKw"
SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"

# Ожидаемые колонки: индекс по позиции (0-based)
COL_ACTIVITY = 6   # G — Вид деятельности
COL_DESC     = 3   # D — описание/детали
COL_SALARY_K = 10  # K
COL_SALARY_L = 11  # L (приоритет)

def parse_salary_value(text: str) -> Optional[int]:
    """
    Парсим сумму: 90000, 90 000, 90k/90к, 90 тыс, 1.2м/1.2 млн, диапазоны.
    Возвращаем максимум из найденных значений.
    """
    import re
    if not text:
        return None
    t = str(text).lower().strip()
    nums: list[int] = []

    # 1) «голые» числа с пробелами/точками/знаком валюты
    for m in re.findall(r"\d[\d\s.,]*", t):
        digits = re.sub(r"[^\d]", "", m)
        if digits:
            nums.append(int(digits))

    # 2) 90k / 90к / 90 тыс
    for val, _ in re.findall(r"(\d+(?:[\s.,]\d+)?)\s*(k|к|тыс)", t):
        try:
            v = int(float(val.replace(" ", "").replace(",", ".").replace("\u00a0",""))*1_000)
            nums.append(v)
        except ValueError:
            pass

    # 3) 1.2m / 1.2м / 1.2 млн
    for val, _ in re.findall(r"(\d+(?:[\s.,]\d+)?)\s*(m|м|млн)", t):
        try:
            v = int(float(val.replace(" ", "").replace(",", ".").replace("\u00a0",""))*1_000_000)
            nums.append(v)
        except ValueError:
            pass

    if not nums:
        return None
    return max(nums)

def fetch_sheet_rows(timeout: int = 20) -> List[List[str]]:
    """Скачиваем CSV первой вкладки без авторизации (таблица должна быть доступна по ссылке)."""
    r = requests.get(SHEET_CSV_URL, timeout=timeout)
    r.raise_for_status()
    # Google даёт CSV в UTF-8
    content = r.content.decode("utf-8")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    # уберём пустые хвосты
    return [row for row in rows if any(cell.strip() for cell in row)]

def pick_vacancies(rows: List[List[str]], role: str, min_salary: int) -> Dict[str, Any]:
    """Фильтрация: сначала по виду деятельности (G), затем по зарплате (L, иначе K)."""
    role_low = role.lower()
    filtered = []
    for i, row in enumerate(rows[1:], start=1):  # пропустим заголовок
        # безопасность индексов
        if len(row) <= max(COL_ACTIVITY, COL_SALARY_L, COL_SALARY_K, COL_DESC):
            continue
        activity = (row[COL_ACTIVITY] or "").lower()
        if role_low not in activity:
            continue
        sL = parse_salary_value(row[COL_SALARY_L]) if row[COL_SALARY_L] else None
        sK = parse_salary_value(row[COL_SALARY_K]) if row[COL_SALARY_K] else None
        salary = sL if sL is not None else sK
        if salary is None:
            continue
        if salary >= min_salary:
            filtered.append({
                "idx": i,
                "salary": salary,
                "activity": row[COL_ACTIVITY],
                "desc": row[COL_DESC],
                "raw_k": row[COL_SALARY_K],
                "raw_l": row[COL_SALARY_L],
            })
    # если ничего не прошло по порогу — возьмём ТОП-5 по зарплате среди всех подходящих по роли
    if not filtered:
        pool = []
        for i, row in enumerate(rows[1:], start=1):
            if len(row) <= max(COL_ACTIVITY, COL_SALARY_L, COL_SALARY_K, COL_DESC):
                continue
            if role_low not in (row[COL_ACTIVITY] or "").lower():
                continue
            sL = parse_salary_value(row[COL_SALARY_L]) if row[COL_SALARY_L] else None
            sK = parse_salary_value(row[COL_SALARY_K]) if row[COL_SALARY_K] else None
            salary = sL if sL is not None else sK
            if salary is None:
                continue
            pool.append({
                "idx": i,
                "salary": salary,
                "activity": row[COL_ACTIVITY],
                "desc": row[COL_DESC],
                "raw_k": row[COL_SALARY_K],
                "raw_l": row[COL_SALARY_L],
            })
        pool.sort(key=lambda x: x["salary"], reverse=True)
        return {"items": pool[:5], "above": False}
    filtered.sort(key=lambda x: x["salary"], reverse=True)
    return {"items": filtered[:5], "above": True}

def fmt_salary_for_user(item: Dict[str, Any]) -> str:
    raw = item["raw_l"] or item["raw_k"] or f'{item["salary"]}'
    return re.sub(r"\s+", " ", raw).strip()

# ---- Хендлеры
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Найти вакансии", callback_data="find")],
    ])
    text = (
        "Привет! Я помогу найти вакансии с самыми высокими зарплатами.\n"
        "Нажми кнопку ниже — подберу лучшие варианты."
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=kb)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    context.user_data.clear()
    return CITY_CHOOSER

async def btn_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == "find":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(c, callback_data=f"city:{c}") for c in CITIES]])
        await query.edit_message_text("Выберите город:", reply_markup=kb)
        context.user_data["state"] = "CHOOSE_CITY"
        return

    if data.startswith("city:"):
        city = data.split(":", 1)[1]
        context.user_data["city"] = city
        kb_rows = [[InlineKeyboardButton(role, callback_data=f"role:{role}")] for role in ROLES]
        await query.edit_message_text(f"Город: {city}\nКем хотите работать?",
                                      reply_markup=InlineKeyboardMarkup(kb_rows))
        context.user_data["state"] = "CHOOSE_ROLE"
        return

    if data.startswith("role:"):
        role = data.split(":", 1)[1]
        context.user_data["role"] = role
        context.user_data["state"] = "AWAIT_SALARY"
        await query.edit_message_text(
            f"Город: {context.user_data.get('city')}\n"
            f"Роль: {role}\n\n"
            "Введите желаемую зарплату в месяц (например: 90 000):"
        )
        return

    if data.startswith("open:"):
        idx = int(data.split(":", 1)[1])
        rows = context.user_data.get("rows_cache", [])
        if not rows or idx <= 0 or idx >= len(rows):
            await query.edit_message_text("Запись недоступна, попробуйте снова /start.")
            return
        row = rows[idx]
        desc = row[3] if len(row) > 3 else "Описание недоступно."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад к вакансиям", callback_data="back_to_list"),
             InlineKeyboardButton("✅ Откликнуться", callback_data=f"apply:{idx}")]
        ])
        await query.edit_message_text(desc, reply_markup=kb)
        return

    if data == "back_to_list":
        results = context.user_data.get("last_results", {})
        await show_results_list(update, context, results)
        return

    if data.startswith("apply:"):
        await query.edit_message_text("Отлично! Я передам ваше желание откликнуться. (демо-режим)")
        return

async def ask_salary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_in = (update.message.text or "").strip()
    want = parse_salary_value(text_in)
    if not want:
        msg = ("Я не понял сумму. Напиши *только цифрами* без слов, например: `90000`\n"
               "Поддерживаются варианты: `90 000`, `90k/90к`, `90 тыс`.")
        await update.message.reply_text(msg, parse_mode="Markdown")
        return SALARY_ASK

    context.user_data["salary"] = want
    # Немедленный отклик
    pretty = f"{want:,}".replace(",", " ")
    await update.message.reply_text(f"Принял сумму: {pretty} ₽. Ищу подходящие вакансии…")

    try:
        rows = fetch_sheet_rows()
    except Exception:
        logger.exception("fetch error")
        await update.message.reply_text("Не удалось получить вакансии. Попробуйте позже.")
        return ConversationHandler.END

    context.user_data["rows_cache"] = rows
    results = pick_vacancies(rows, context.user_data.get("role",""), want)
    context.user_data["last_results"] = results

    items = results.get("items") or []
    if not items:
        await update.message.reply_text("Пока нет подходящих вакансий. Попробуйте позже.")
        return ConversationHandler.END

    await show_results_list(update, context, results, want)
    return SHOW_RESULTS

async def show_results_list(update: Update, context: ContextTypes.DEFAULT_TYPE, results: Dict[str, Any], want: Optional[int]=None):
    items = results["items"]
    above = results["above"] if want is None else any(i["salary"] >= want for i in items)
    if not items:
        await (update.callback_query or update.message).reply_text("Пока нет подходящих вакансий. Попробуйте позже.")
        return

    header = "🎉 Ура, я нашёл зарплату выше!" if above else "😕 К сожалению, не нашли вакансии с такой зарплатой, но есть другие варианты:"
    lines = []
    btn_rows = []
    for n, it in enumerate(items, start=1):
        lines.append(f"{n}) {it['activity']} — {fmt_salary_for_user(it)}")
        btn_rows.append([InlineKeyboardButton(f"{n}) Открыть", callback_data=f"open:{it['idx']}")])

    text = header + "\n\n" + "\n".join(lines) + "\n\nВыберите вакансию:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(btn_rows))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(btn_rows))

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await (update.message or update.callback_query).reply_text("Диалог завершён. Нажмите /start чтобы начать заново.")
    return ConversationHandler.END

def main():
    app = Application.builder().token(TOKEN).build()
    # команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    # кнопки/текст
    app.add_handler(CallbackQueryHandler(btn_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    async def _on_start(_: Application):
        me = await app.bot.get_me()
        print(f"[bot] online: @{me.username}", flush=True)

    app.post_init = _on_start
    app.run_polling()
print("[bot] online")
[bot] crashed with exception:
", flush=True)
        traceback.print_exc()
        sys.exit(1)
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    if state == "AWAIT_SALARY":
        return await ask_salary(update, context)
    # можно добавить другие состояния позже
    return None


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await (update.message or update.callback_query).reply_text("pong")
