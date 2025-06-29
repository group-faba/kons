# server.py
import os
import json
import logging
from datetime import datetime

from flask import Flask, request, abort
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Bot

logging.basicConfig(level=logging.INFO)

# Telegram
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_TOKEN"]
ADMIN_CHAT_ID      = os.environ["ADMIN_CHAT_ID"]
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Google Sheets
SHEET_ID   = os.environ["SHEET_ID"]
CREDS_JSON = json.loads(os.environ["GSPREAD_CREDENTIALS_JSON"])
SCOPES     = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
creds      = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc         = gspread.authorize(creds)
sheet      = gc.open_by_key(SHEET_ID).worksheet("Лист1")

app = Flask(__name__)

@app.route("/")
def healthcheck():
    return "OK", 200

@app.route("/submit", methods=["POST"])
def submit():
    if not request.is_json:
        abort(400, "Expected JSON")
    data = request.get_json()
    fio  = data.get("fio", "").strip()
    city = data.get("city", "").strip()
    if not fio or not city:
        abort(400, "Missing fio or city")

    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    sheet.append_row([timestamp, fio, city])

    text = (
        "🆕 Новая заявка из iOS-приложения:\n"
        f"• Время: {timestamp} UTC\n"
        f"• ФИО: {fio}\n"
        f"• Город: {city}"
    )
    bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)

    return {"status":"ok"}, 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
