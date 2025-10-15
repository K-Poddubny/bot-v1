import os
import sys
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    sys.stderr.write("[!] Не найден TELEGRAM_BOT_TOKEN. Укажи его в .env\n")
    sys.exit(1)

from telegram import Update  # noqa: E402
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters  # noqa: E402

GREETING = (
    "Привет! Я помогу тебе найти работу с самыми высокими зарплатами. "
    "Напиши, что тебя интересует — начнём!"
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(GREETING)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(GREETING)

def main() -> None:
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("[i] Бот запущен (long polling). Нажми Ctrl+C для остановки.")
    app.run_polling()

if __name__ == "__main__":
    main()
