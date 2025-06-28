# bot.py
import os
import json
import logging
import threading

from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# 1) Логирование
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["TELEGRAM_TOKEN"]
PORT  = int(os.environ.get("PORT", "8080"))

# 2) Flask для healthcheck (Render ждёт открытый порт)
app = Flask(__name__)
@app.route("/")
def health():
    return "OK", 200

# 3) Хендлер /webapp — шлёт кнопку
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

# 4) Хендлер приема данных из WebApp
async def on_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw  = update.message.web_app_data.data
    data = json.loads(raw)
    fio  = data.get("fio", "")
    city = data.get("city", "")
    await update.message.reply_text(f"✅ Получили: ФИО={fio}, Город={city}")

# 5) Создаем Application и регистрируем хендлеры
application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler("webapp", cmd_webapp))
application.add_handler(
    MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data)
)

# 6) Функция для polling
def start_polling():
    application.run_polling(drop_pending_updates=True)

# 7) Точка входа
if __name__ == "__main__":
    # Запускаем polling в фоне
    threading.Thread(target=start_polling, daemon=True).start()
    # А сам process слушает Flask-порт
    app.run(host="0.0.0.0", port=PORT)
