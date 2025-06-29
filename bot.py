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

# -------------------------
#  Настройки и авторизация
# -------------------------
logging.basicConfig(level=logging.INFO)

TOKEN      = os.environ["TELEGRAM_TOKEN"]
SHEET_ID   = os.environ["SHEET_ID"]
CREDS_JSON = json.loads(os.environ["GSPREAD_CREDENTIALS_JSON"])
APP_URL    = os.environ["APP_URL"]       # https://ваш-домен.onrender.com
PORT       = int(os.environ.get("PORT", "8080"))

# Google Sheets
SCOPES    = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds     = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc        = gspread.authorize(creds)
worksheet = gc.open_by_key(SHEET_ID).worksheet("Лист1")

# Telegram Bot & Application
bot        = Bot(token=TOKEN)
application = Application.builder().token(TOKEN).build()

# -------------------------
#  Flask для webhook и health
# -------------------------
app = Flask(__name__)

@app.route("/")
def health():
    return "OK", 200

@app.route(f"/webhook/{TOKEN}", methods=["POST"])
def telegram_webhook():
    """Пришёл POST от Telegram → прокинуть в application."""
    data = request.get_json(force=True)
    update = Update.de_json(data, bot)
    application.process_update(update)
    return "OK", 200

# -------------------------
#  Handlers
# -------------------------
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

async def on_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw  = update.message.web_app_data.data
    data = json.loads(raw)
    fio  = data.get("fio", "").strip()
    city = data.get("city", "").strip()

    # Записать в Google Sheets
    worksheet.append_row([fio, city])

    await update.message.reply_text(
        f"✅ Получили заявку: ФИО={fio}, Город={city}"
    )

# Регистрируем хендлеры
application.add_handler(CommandHandler("webapp", cmd_webapp))
application.add_handler(
    MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data)
)

# -------------------------
#  Запуск
# -------------------------
if __name__ == "__main__":
    # 1) Ставим webhook у Telegram
    webhook_url = f"{APP_URL}/webhook/{TOKEN}"
    bot.set_webhook(webhook_url)
    logging.info("Webhook установлен: %s", webhook_url)

    # 2) Запускаем Flask
    app.run(host="0.0.0.0", port=PORT)
