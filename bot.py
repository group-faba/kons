import os
import json
import logging

from flask import Flask, request
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- Настройки ---
logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ["TELEGRAM_TOKEN"]
SHEET_ID   = os.environ["SHEET_ID"]
CREDS_JSON = json.loads(os.environ["GSPREAD_CREDENTIALS_JSON"])
APP_URL    = os.environ["APP_URL"]       # https://kons.onrender.com
PORT       = int(os.environ.get("PORT", "8080"))

# --- Google Sheets ---
SCOPES    = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
creds     = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
worksheet = gspread.authorize(creds).open_by_key(SHEET_ID).worksheet("Лист1")

# --- Telegram Bot/Application ---
bot         = Bot(token=TOKEN)
application = Application.builder().token(TOKEN).build()

# --- Flask для webhook & healthcheck ---
app = Flask(__name__)

@app.route("/")
def health():
    return "OK", 200

@app.route(f"/webhook/{TOKEN}", methods=["POST"])
def webhook_handler():
    upd = Update.de_json(request.get_json(force=True), bot)
    application.process_update(upd)
    return "OK", 200

# --- Handlers ---
async def cmd_webapp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[ InlineKeyboardButton("Заполнить форму", web_app=WebAppInfo(url=f"{APP_URL}/")) ]]
    await update.message.reply_text("Нажми, чтобы открыть форму:", reply_markup=InlineKeyboardMarkup(kb))

async def on_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = json.loads(update.message.web_app_data.data)
    fio  = data.get("fio","").strip()
    city = data.get("city","").strip()
    worksheet.append_row([fio, city])
    await update.message.reply_text(f"✅ Получили: ФИО={fio}, Город={city}")

application.add_handler(CommandHandler("webapp", cmd_webapp))
application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data))

# --- Запуск ---
if __name__ == "__main__":
    # Устанавливаем webhook
    url = f"{APP_URL}/webhook/{TOKEN}"
    bot.set_webhook(url)
    logging.info("Webhook установлен на %s", url)
    # Запускаем Flask
    app.run(host="0.0.0.0", port=PORT)
