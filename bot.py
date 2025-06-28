import os
import logging
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

# --- Логирование
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ['TELEGRAM_TOKEN']
PORT = int(os.environ.get('PORT', '8080'))

# --- Flask healthcheck
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# --- КНОПКА мини-приложения ---
async def send_webapp_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton(
            "Открыть мини-приложение",
            web_app=WebAppInfo(url="https://telegram-kons.vercel.app/")  # <-- твоя ссылка
        )]
    ]
    await update.message.reply_text(
        "Запусти мини-приложение для записи на консультацию:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# --- ПРИЁМ данных из WebApp ---
async def handle_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    import json
    logging.info("handle_webapp_data CALLED")
    try:
        data = update.message.web_app_data.data
        logging.info(f"DATA: {data}")
        form = json.loads(data)
        fio = form.get("fio", "")
        city = form.get("city", "")
        await update.message.reply_text(f"Спасибо! Получено: {fio}, {city}")
    except Exception as e:
        logging.exception("Ошибка в handle_webapp_data")

# --- PTB Application ---
application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler("webapp", send_webapp_button))
application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))

# --- Flask + polling для Render ---
def run_flask():
    app.run(host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
