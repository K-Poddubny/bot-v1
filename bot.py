import os, sys
from dotenv import load_dotenv
load_dotenv()
t=os.getenv("TELEGRAM_BOT_TOKEN")
if not t:
    sys.exit(1)
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
G="Привет! Я помогу тебе найти работу с самыми высокими зарплатами. Напиши, что тебя интересует — начнём!"
async def start(u:Update,c:ContextTypes.DEFAULT_TYPE): await u.message.reply_text(G)
async def handle(u:Update,c:ContextTypes.DEFAULT_TYPE): await u.message.reply_text(G)
def main():
    a=Application.builder().token(t).build()
    a.add_handler(CommandHandler("start", start))
    a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    a.run_polling()
if __name__=="__main__": main()
