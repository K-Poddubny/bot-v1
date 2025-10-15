
from telegram import constants

async def send_text(update, text, reply_markup=None):
    """
    Унифицированная отправка сообщений и из message, и из callback_query.
    """
    cq = getattr(update, "callback_query", None)
    if cq:
        try:
            await cq.answer()
        except Exception:
            pass
        # reply в чат, откуда пришла кнопка
        return await send_text(update, 
            text,
            reply_markup=reply_markup,
            parse_mode=getattr(constants.ParseMode, "HTML", None)
        )
    msg = getattr(update, "message", None)
    if msg:
        return await send_text(update, 
            text,
            reply_markup=reply_markup,
            parse_mode=getattr(constants.ParseMode, "HTML", None)
        )
    # Фолбек на бот, если что-то экзотическое
    chat_id = None
    try:
        chat_id = update.effective_chat.id
    except Exception:
        pass
    if chat_id and "context" in update.to_dict():
        return await update.to_dict()["context"].bot.send_message(
            chat_id=chat_id, text=text, reply_markup=reply_markup,
            parse_mode=getattr(constants.ParseMode, "HTML", None)
        )
    return None

import os
import re
import csv
import io
import logging
import warnings
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv
load_dotenv()  # если есть .env — подхватим TELEGRAM_BOT_TOKEN

# приглушим ворнинги от urllib3 OpenSSL на маке
try:
    from urllib3.exceptions import NotOpenSSLWarning
    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
except Exception:
    pass

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)
import httpx

# ===== Настройки =====
# если переменной нет — используем твой токен как фолбэк
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or "8449257401:AAFLCuuyBi1Mmd63gkF6ujB1hGSdAFyn_9w"

SHEET_ID = "1_KIjSrpBbc3xv-fuobapE2xj12kR6-tUmZEiLe41NKw"
CSV_URLS = [
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv",
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv",
]

CITIES = ["Москва"]
ROLES = ["Водитель", "Курьер", "Разнорабочий", "Работник торгового зала"]

# Индексы столбцов (0-based)
COL = {
    "TITLE": 1,      # B
    "EMPLOYER": 2,   # C
    "DESC": 3,       # D
    "ACTIVITY": 6,   # G
    "SAL_K": 10,     # K
    "SAL_L": 11,     # L
}

# Логи тише
logging.basicConfig(level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
log = logging.getLogger("bot")

# ===== Утилиты =====
def parse_salary_value(text: str) -> Optional[int]:
    """
    Понимаем 90000, 90 000, 90k/90к, 90 тыс, 1.2м/1.2 млн, диапазоны (берём максимум).
    """
    if not text:
        return None
    t = str(text).lower().strip()
    nums: List[int] = []

    # «голые» числа
    for m in re.findall(r"\d[\d\s.,]*", t):
        digits = re.sub(r"[^\d]", "", m)
        if digits:
            try:
                nums.append(int(digits))
            except ValueError:
                pass

    # k/к/тыс
    for val, _unit in re.findall(r"(\d+(?:[\s.,]\d+)?)\s*(k|к|тыс)", t):
        try:
            v = int(float(val.replace(" ", "").replace(",", ".").replace("\u00a0", "")) * 1_000)
            nums.append(v)
        except ValueError:
            pass

    # m/м/млн
    for val, _unit in re.findall(r"(\d+(?:[\s.,]\d+)?)\s*(m|м|млн)", t):
        try:
            v = int(float(val.replace(" ", "").replace(",", ".").replace("\u00a0", "")) * 1_000_000)
            nums.append(v)
        except ValueError:
            pass

    if not nums:
        return None
    return max(nums)

async def http_get_bytes(url: str) -> Optional[bytes]:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url)
            if r.status_code == 200 and r.content:
                return r.content
    except Exception:
        log.exception("GET failed: %s", url)
    return None

async def fetch_sheet_rows() -> List[List[str]]:
    """Тянем CSV из таблицы (перебором вариантов урла)."""
    for url in CSV_URLS:
        data = await http_get_bytes(url)
        if not data:
            continue
        try:
            text = data.decode("utf-8", errors="ignore")
            reader = csv.reader(io.StringIO(text))
            rows = [row for row in reader]
            if rows:
                return rows
        except Exception:
            log.exception("CSV parse failed")
    return []

def salary_from_row(row: List[str]) -> Optional[int]:
    """Сначала L, если пусто — K."""
    l = row[COL["SAL_L"]].strip() if len(row) > COL["SAL_L"] else ""
    k = row[COL["SAL_K"]].strip() if len(row) > COL["SAL_K"] else ""
    src = l or k
    return parse_salary_value(src)

def role_matches(row: List[str], role: str) -> bool:
    act = row[COL["ACTIVITY"]].lower() if len(row) > COL["ACTIVITY"] else ""
    return role.lower() in act

def pick_vacancies(rows: List[List[str]], role: str, want: int) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    for i, row in enumerate(rows):
        if i == 0:  # заголовок
            continue
        if not role_matches(row, role):
            continue
        smax = salary_from_row(row)
        if smax is None:
            continue
        items.append({
            "idx": i,
            "salary": smax,
            "title": row[COL["TITLE"]] if len(row) > COL["TITLE"] else "Вакансия",
            "employer": row[COL["EMPLOYER"]] if len(row) > COL["EMPLOYER"] else "",
            "desc": row[COL["DESC"]] if len(row) > COL["DESC"] else "",
        })
    items.sort(key=lambda x: x["salary"], reverse=True)
    top = items[:5]
    return {"items": top, "found_higher": any(x["salary"] >= want for x in top)}

def pretty_rub(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " ₽"

async def show_results_list(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            results: Dict[str, Any], want: Optional[int] = None):
    items = results.get("items", [])
    found_higher = results.get("found_higher", False)

    if not items:
        await send_text(update, 
            "Пока нет подходящих вакансий. Попробуйте позже.")
        return

    header = "Ура! Я нашёл зарплату выше твоего запроса 🎉" if (want and found_higher) \
             else "К сожалению, не нашёл именно такую зарплату, но есть варианты ниже:"

    lines = [header, ""]
    kb = []
    for it in items:
        title = it["title"] or "Вакансия"
        line = f"• {title} — {pretty_rub(it['salary'])}"
        lines.append(line)
        kb.append([InlineKeyboardButton(line[2:], callback_data=f"open:{it['idx']}")])

    await send_text(update, 
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb)
    )

# ===== Хэндлеры =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔎 Найти вакансии", callback_data="find")]]
    )
    await send_text(update, 
        "Привет! Я помогу тебе найти работу с самыми высокими зарплатами. "
        "Напиши, что тебя интересует — начнём!",
        reply_markup=kb
    )

async def btn_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    await q.answer()

    if data == "find":
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(c, callback_data=f"city:{c}")] for c in CITIES]
        )
        await q.edit_message_text("Выберите город:", reply_markup=kb)
        context.user_data["state"] = "CHOOSE_CITY"
        return

    if data.startswith("city:"):
        city = data.split(":", 1)[1]
        context.user_data["city"] = city
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(r, callback_data=f"role:{r}")] for r in ROLES]
        )
        await q.edit_message_text(f"Город: {city}\nКем хотите работать?", reply_markup=kb)
        context.user_data["state"] = "CHOOSE_ROLE"
        return

    if data.startswith("role:"):
        role = data.split(":", 1)[1]
        context.user_data["role"] = role
        context.user_data["state"] = "AWAIT_SALARY"
        await q.edit_message_text(
            f"Город: {context.user_data.get('city')}\nРоль: {role}\n\n"
            "Введите желаемую зарплату в месяц (например: 90 000):"
        )
        return

    if data.startswith("open:"):
        idx = int(data.split(":", 1)[1])
        rows = context.user_data.get("rows_cache") or []
        if not rows or idx <= 0 or idx >= len(rows):
            await q.edit_message_text("Запись недоступна, попробуйте снова /start.")
            return
        row = rows[idx]
        desc = row[COL["DESC"]] if len(row) > COL["DESC"] else "Описание недоступно."
        desc = format_vacancy_desc(desc)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад к вакансиям", callback_data="back_to_list"),
             InlineKeyboardButton("✅ Откликнуться", callback_data=f"apply:{idx}")]
        ])
        await q.edit_message_text(desc, reply_markup=kb)
        return

    if data == "back_to_list":
        results = context.user_data.get("last_results", {})
        await show_results_list(update, context, results)
        return

    if data.startswith("apply:"):
        await q.edit_message_text("Отлично! Я передам ваше желание откликнуться. (демо)")
        return

async def ask_salary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_in = (update.message.text or "").strip()
    want = parse_salary_value(text_in)
    if not want:
        await send_text(update, 
            "Я не понял сумму. Напиши *только цифрами* без слов, например: `90000`.\n"
            "Поддерживаются: `90 000`, `90k/90к`, `90 тыс`, `1.2м`.",
            parse_mode="Markdown"
        )
        return

    context.user_data["salary"] = want
    await send_text(update, f"Принял сумму: {pretty_rub(want)}. Ищу подходящие вакансии…")

    rows = await fetch_sheet_rows()
    if not rows:
        await send_text(update, "Не удалось получить вакансии. Попробуйте позже.")
        return
    context.user_data["rows_cache"] = rows

    role = context.user_data.get("role", "")
    results = pick_vacancies(rows, role, want)
    context.user_data["last_results"] = results
    await show_results_list(update, context, results, want)

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") == "AWAIT_SALARY":
        await ask_salary(update, context)
        return
    # игнорируем свободный ввод
    return

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_text(update, "pong")



def format_vacancy_desc(raw: str) -> str:
    """Простой и безопасный форматтер.
    - Чистит HTML (<br>, любые теги).
    - Убирает маркдаун-скобки (** ` и т.п.), длинные «----».
    - Режет только «адресные простыни» после заголовков вида «Ищем водител… по адрес…».
    - Любая непустая строка становится пунктом «• …»; заголовки — только по ключам/двоеточию.
    """
    import re
    if not raw:
        return "Описание недоступно."

    t = raw.replace("\r\n", "\n").replace("\r", "\n")

    # HTML → текст
    t = re.sub(r"(?is)<br\s*/?>", "\n", t)
    t = re.sub(r"(?is)</?p[^>]*>", "\n", t)
    t = re.sub(r"(?is)</?(ul|ol|li)[^>]*>", "\n", t)
    t = re.sub(r"(?is)</[^>]+>", " ", t)   # любые прочие теги → пробел
    t = re.sub(r"[\t\xa0]", " ", t)

    # Маркдаун/мусор
    t = re.sub(r"[`*]{1,3}", "", t)
    t = re.sub(r"_{2,}", "_", t)
    t = re.sub(r"^\s*[-–—]{3,}\s*$", "", t, flags=re.MULTILINE)  # горизонтальные «линейки»
    t = re.sub(r"\s*[-–—]{2,}\s*", " — ", t)                      # ---, -- → « — »

    # Заголовки по ключам
    hdr_rules = [
        (r"^(мы\s+предлагаем|условия|что\s+предлагаем|что\s+получишь)\b", "💼 Мы предлагаем"),
        (r"^(обязанности|что\s+нужно\s+делать|что\s+делать|чем\s+заниматься)\b", "🧰 Обязанности"),
        (r"^(требования|мы\s+ожидаем|кандидат|что\s+нужно)\b", "✅ Требования"),
        (r"^(оплата|зарплата|доход|компенсации|бонусы|условия\s+оплаты)\b", "💰 Оплата и бонусы"),
        (r"^(как\s+откликнуться|что\s+делать\s+дальше|как\s+начать|оформление)\b", "📩 Как откликнуться"),
    ]
    def detect_header(line: str):
        low = line.lower()
        for pat, h in hdr_rules:
            if re.search(pat, low):
                return h
        if len(line) <= 100 and line.endswith(":"):
            return "📌 " + line[:-1]
        return None

    # Чистка лидирующих маркеров
    def unbullet(s: str) -> str:
        s = s.strip()
        s = re.sub(r"^[\-–—•*·\u2022]+[)\.]?\s*", "", s)
        s = re.sub(r"^\d+\)\s*", "", s)        # 1)
        s = re.sub(r"^\(\d+\)\s*", "", s)     # (1)
        return re.sub(r"\s{2,}", " ", s).strip()

    raw_lines = [unbullet(x) for x in t.split("\n")]

    # Схлопываем пустые
    lines = []
    for ln in raw_lines:
        if ln == "" and (not lines or lines[-1] == ""):
            continue
        lines.append(ln)

    out, buf = [], []

    def flush():
        if not buf:
            return
        out.extend([f"• {x}" for x in buf if x])
        buf.clear()

    # Скип адресных простыней ТОЛЬКО после явного заголовка
    skip_addr = False
    addr_kw = r"(ул\.|просп\.|шосс\.|пл\.|пер\.|бул\.|км\b|стр\.|дом\b|д\.|корп\.|к\.|лит\.|пр-т\.|ш\.|наб\.|пр\.)"

    for ln in lines:
        if skip_addr:
            if ln == "" or detect_header(ln):
                skip_addr = False
                if ln == "":
                    continue
            else:
                if ln.startswith("|") or re.fullmatch(r"[\|\-–—\s]+", ln) or re.search(addr_kw, ln, re.IGNORECASE) or ln.count(",") >= 2:
                    continue
                skip_addr = False  # вышли из адресного текста

        if ln == "":
            flush()
            if out and out[-1] != "":
                out.append("")
            continue

        low = ln.lower()
        if ("ищем" in low and "водител" in low and ("по адрес" in low or "адреса" in low)):
            flush()          # заголовок не показываем
            skip_addr = True # и включаем скип адресов
            continue

        hdr = detect_header(ln)
        if hdr:
            flush()
            if out and out[-1] != "":
                out.append("")
            out.append(hdr)
            out.append("")
            continue

        buf.append(ln)

    flush()

    result = "\n".join([ln for i, ln in enumerate(out) if not (ln == "" and (i == 0 or out[i-1] == ""))]).strip()
    return result or "Описание недоступно."

async def back_to_results(update, context):
    """Возврат к списку последних найденных вакансий."""
    query = update.callback_query
    if query:
        await query.answer()
    results = context.user_data.get("last_results") or []
    role    = context.user_data.get("role") or ""
    city    = context.user_data.get("city") or "Москва"
    await show_results_list(update, context, results, city=city, role=role)

def main():
    app = Application.builder().token(TOKEN).build()

    # команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    # кнопки и текст
    app.add_handler(CallbackQueryHandler(btn_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    async def _post_init(_: Application):
        # снимаем вебхук на всякий случай (чтобы polling не конфликтовал)
        try:
            await app.bot.delete_webhook(drop_pending_updates=False)
        except Exception:
            pass
        me = await app.bot.get_me()
        print(f"[✓] Bot online: @{me.username}", flush=True)

    app.post_init = _post_init
    app.run_polling()

if __name__ == "__main__":
    main()
