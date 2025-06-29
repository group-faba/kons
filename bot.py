import os, json, logging, threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["TELEGRAM_TOKEN"]
PORT  = int(os.environ.get("PORT", "8080"))

# --- Flask healthcheck ---
app = Flask(__name__)
@app.route("/")# bot.py

import os
import json
import logging
import threading
from datetime import datetime

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# --- Логирование ---
logging.basicConfig(level=logging.INFO)

# --- Переменные окружения ---
TOKEN         = os.environ["TELEGRAM_TOKEN"]
SHEET_ID      = os.environ["SHEET_ID"]
CREDS_JSON    = json.loads(os.environ["GSPREAD_CREDENTIALS_JSON"])
PORT          = int(os.environ.get("PORT", "8080"))

# --- Инициализация Google Sheets ---
SCOPES    = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds     = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc        = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SHEET_ID)
worksheet   = spreadsheet.worksheet("Лист1")  # <- укажите имя вашего листа

# --- Flask healthcheck для Render ---
app = Flask(__name__)

@app.route("/")
def health():
    return "OK", 200

# --- Хендлер /webapp: выдаём кнопку для мини-приложения ---
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

# --- Хендлер приёма данных из WebApp и запись в Google Sheets ---
async def handle_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.web_app_data.data
    logging.info("WEB_APP_DATA payload: %s", raw)
    data = json.loads(raw)
    fio  = data.get("fio", "").strip()
    city = data.get("city", "").strip()
    user_id = update.effective_user.id
    ts = datetime.now().isoformat(sep=" ", timespec="seconds")

    # Записываем новую строку в Google Sheets
    worksheet.append_row([ts, fio, city, user_id])

    # Отвечаем пользователю
    await update.message.reply_text(f"✅ Заявка принята:\nФИО: {fio}\nГород: {city}")

# --- Сборка Telegram-приложения ---
application = (
    ApplicationBuilder()
    .token(TOKEN)
    .build()
)

application.add_handler(CommandHandler("webapp", cmd_webapp))
application.add_handler(
    MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data)
)

# --- Функция запуска polling ---
def run_bot():
    logging.info("🚀 Starting Telegram polling…")
    application.run_polling(drop_pending_updates=True)

# --- Точка входа ---
if __name__ == "__main__":
    # 1) Запускаем Flask для healthcheck в фоне
    t = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT), daemon=True)
    t.start()

    # 2) Запускаем polling в главном потоке
    run_bot()
def health():
    return "OK", 200

# --- Ваш рабочий хендлер /webapp ---
async def send_webapp_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("Открыть мини-приложение",
             web_app=WebAppInfo(url="https://telegram-kons.vercel.app/"))]]
    await update.message.reply_text(
        "Запусти мини-приложение для записи на консультацию:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# --- Хендлер приёма данных из WebApp ---
async def handle_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = json.loads(update.message.web_app_data.data)
    fio  = data.get("fio", "")
    city = data.get("city", "")
    await update.message.reply_text(f"✅ Получили: ФИО={fio}, Город={city}")

# --- Настройка бота ---
application = (
    ApplicationBuilder()
    .token(TOKEN)
    .build()
)
application.add_handler(CommandHandler("webapp", send_webapp_button))
application.add_handler(
    MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data)
)

if __name__ == "__main__":
    # 1) Flask в фоне
    t = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=PORT),
        daemon=True
    )
    t.start()
    # 2) Polling в главном потоке
    application.run_polling(drop_pending_updates=True)
