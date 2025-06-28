import os
import json
import threading
import logging

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

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ["TELEGRAM_TOKEN"]
PORT  = int(os.environ.get("PORT", "8080"))

# 1) Flask для healthcheck — чтобы Render увидел открытый порт
app = Flask(__name__)
@app.route("/")
def health():
    return "OK", 200

# 2) Команда /webapp — выдаёт кнопку
async def cmd_webapp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[
        InlineKeyboardButton(
            "Заполнить форму",
            web_app=WebAppInfo(url="https://telegram-kons.vercel.app")
        )
    ]]
    await update.message.reply_text(
        "Нажми, чтобы открыть форму:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# 3) Обработка пришедших из WebApp данных
async def on_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw  = update.message.web_app_data.data
    logging.info("🔔 WEB_APP_DATA received: %s", raw)
    data = json.loads(raw)
    fio  = data.get("fio", "<не задано>")
    city = data.get("city", "<не задано>")
    await update.message.reply_text(f"✅ Получили: ФИО={fio}, Город={city}")

# 4) Собираем Telegram-приложение
application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler("webapp", cmd_webapp))
application.add_handler(
    MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data)
)

def start_polling():
    logging.info("⚡️ Starting polling thread…")
    application.run_polling(drop_pending_updates=True)

# 5) Точка входа
if __name__ == "__main__":
    threading.Thread(target=start_polling, daemon=True).start()
    logging.info("🚀 Flask healthcheck on port %s", PORT)
    app.run(host="0.0.0.0", port=PORT)
