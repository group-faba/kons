import os, json, logging, threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["TELEGRAM_TOKEN"]
PORT  = int(os.environ.get("PORT", "8080"))

# --- Flask healthcheck ---
app = Flask(__name__)
@app.route("/")
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
