import os
import json
import logging

from flask import Flask, request
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- Логирование ---
logging.basicConfig(level=logging.INFO)

# --- Переменные окружения ---
TOKEN      = os.environ["TELEGRAM_TOKEN"]
SHEET_ID   = os.environ["SHEET_ID"]
CREDS_JSON = json.loads(os.environ["GSPREAD_CREDENTIALS_JSON"])
PORT       = int(os.environ.get("PORT", "8080"))
APP_URL    = os.environ.get("APP_URL")  # например, "https://kons.onrender.com"

# --- Google Sheets ---
SCOPES      = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds       = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc          = gspread.authorize(creds)
worksheet   = gc.open_by_key(SHEET_ID).worksheet("Лист1")

# --- Flask для вебхуков и healthcheck ---
app = Flask(__name__)

@app.route("/")
def health():
    return "OK", 200

@app.route(f"/webhook/{TOKEN}", methods=["POST"])
def telegram_webhook():
    """Принимаем POST от Telegram и прокидываем в Application."""
    update = Update.de_json(request.get_json(force=True), bot)
    application.process_update(update)
    return "OK", 200

# --- Телеграм-бот handlers ---
async def cmd_webapp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[
        InlineKeyboardButton(
            "Заполнить форму",
            web_app=WebAppInfo(url=f"{APP_URL}/")
        )
    ]]
    await update.message.reply_text(
        "Нажми, чтобы открыть форму:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def handle_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw  = update.message.web_app_data.data
    data = json.loads(raw)
    fio  = data.get("fio", "").strip()
    city = data.get("city", "").strip()

    # Запись в Google Sheets
    worksheet.append_row([fio, city])

    await update.message.reply_text(f"✅ Заявка принята: ФИО={fio}, Город={city}")

# --- Настройка Telegram Application (v.21+) ---
bot = Bot(token=TOKEN)
application = Application.builder().token(TOKEN).build()

application.add_handler(CommandHandler("webapp", cmd_webapp))
application.add_handler(
    MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data)
)

if __name__ == "__main__":
    # 1) Включаем webhook у Telegram на наш endpoint
    webhook_url = f"{APP_URL}/webhook/{TOKEN}"
    bot.set_webhook(webhook_url)
    logging.info("Webhook set to %s", webhook_url)

    # 2) Запускаем Flask (он же и слушает webhook POST-ы)
    app.run(host="0.0.0.0", port=PORT)
