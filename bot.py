import os
import json
import logging

from flask import Flask, request
import gspread
from oauth2client.service_account import ServiceAccountCredentials

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
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from datetime import datetime, timedelta
import threading

# =========== Логирование ===========
logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ["TELEGRAM_TOKEN"]
SHEET_ID   = os.environ["SHEET_ID"]
CREDS_JSON = json.loads(os.environ["GSPREAD_CREDENTIALS_JSON"])
PORT       = int(os.environ.get("PORT", "8080"))

# =========== Подключение к Google Sheets ===========
SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds  = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc     = gspread.authorize(creds)
sheet  = gc.open_by_key(SHEET_ID)

def append_to_sheet(values: list):
    """Добавить строку в первый лист."""
    ws = sheet.worksheet("Лист1")
    ws.append_row(values)

# =========== Flask для healthcheck + приём iOS ===========
app = Flask(__name__)

@app.route("/")
def health():
    return "OK", 200

@app.route("/submit", methods=["POST"])
def submit_from_ios():
    """
    Ожидаем JSON:
      { "fio": "...", "city": "...", "chat_id": 123456789 }
    """
    data = request.get_json(force=True)
    fio  = data.get("fio", "")
    city = data.get("city", "")
    chat = data.get("chat_id")  # int или None

    # 1) Запишем в таблицу
    append_to_sheet([fio, city])

    # 2) При желании уведомим Telegram-чат
    if chat:
        try:
            application.bot.send_message(
                chat_id=chat,
                text=f"📥 Новая заявка из iOS:\nФИО: {fio}\nГород: {city}"
            )
        except Exception:
            pass

    return {"status": "ok"}, 200

# =========== Хендлеры вашего бота (register, time, выбор спеца...) ===========
# Здесь скопируйте весь ваш предыдущий код ConversationHandler’ов.
# Я покажу лишь заглушку для /start и /webapp:

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! /webapp — форма, /register — регистрация.")

async def cmd_webapp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[
        InlineKeyboardButton(
            "Открыть мини-приложение",
            web_app=WebAppInfo(url="https://telegram-kons.vercel.app/")
        )
    ]]
    await update.message.reply_text(
        "Нажми, чтобы открыть форму:", reply_markup=InlineKeyboardMarkup(kb)
    )

async def on_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Принимаем callback из WebApp внутри Telegram."""
    raw  = update.message.web_app_data.data
    data = json.loads(raw)
    fio  = data.get("fio", "")
    city = data.get("city", "")
    # Записываем в таблицу и отвечаем
    append_to_sheet([fio, city, update.effective_user.id])
    await update.message.reply_text(f"✅ Получили: ФИО={fio}, Город={city}")

# =========== Собираем приложение ===========
application = ApplicationBuilder().token(TOKEN).build()

# Регистрация базовых хендлеров:
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(CommandHandler("webapp", cmd_webapp))
application.add_handler(
    MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data)
)

# TODO: сюда же добавьте все ваши ConversationHandler’ы conv_reg, conv_time, conv_main и т. п.
# application.add_handler(conv_reg)
# application.add_handler(conv_time)
# application.add_handler(conv_main)

# =========== Запуск Flask + polling ===========
def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    # 1) Бэкграунд для healthcheck и /submit
    threading.Thread(target=run_flask, daemon=True).start()
    # 2) Поллинг Telegram
    application.run_polling(drop_pending_updates=True)
