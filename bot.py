# bot.py
import os, json, logging, threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ['TELEGRAM_TOKEN']
PORT  = int(os.environ.get('PORT', '8080'))

# Flask healthcheck
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# Хендлер /webapp — шлёт кнопку
async def cmd_webapp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[
        InlineKeyboardButton(
            "Заполнить форму",
            web_app=WebAppInfo(url="https://telegram-kons.vercel.app/")
        )
    ]]
    await update.message.reply_text(
        "Нажми, чтобы открыть форму:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# Хендлер приёма данных из WebApp
async def on_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    payload = update.message.web_app_data.data  # <- сюда придёт JSON
    data    = json.loads(payload)
    fio     = data.get("fio", "")
    city    = data.get("city", "")
    await update.message.reply_text(
        f"✅ Получили: ФИО={fio}, Город={city}"
    )

# Собираем бота
application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler("webapp",   cmd_webapp))
application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data))

# Запуск polling в фоне, Flask — в главном потоке
def start_polling():
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    threading.Thread(target=start_polling, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
