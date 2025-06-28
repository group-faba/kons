import os, json, threading, logging
from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, Update
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

# 1) Healthcheck для Render
app = Flask(__name__)
@app.route("/")
def health():
    return "OK", 200

# 2) /webapp — кнопка
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

# 3) Обработка WEB_APP_DATA
async def on_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw  = update.message.web_app_data.data
    data = json.loads(raw)
    fio  = data.get("fio", "")
    city = data.get("city", "")
    await update.message.reply_text(f"✅ Получили: ФИО={fio}, Город={city}")

# 4) Собираем и запускаем
application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler("webapp", cmd_webapp))
application.add_handler(
    MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data)
)

def start_polling():
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    threading.Thread(target=start_polling, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
