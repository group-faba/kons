import os, json, logging
from flask import Flask, request
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["TELEGRAM_TOKEN"]
WEBAPP_URL = "https://telegram-kons.vercel.app/"
HOSTNAME = os.environ["RENDER_EXTERNAL_HOSTNAME"]

bot = Bot(TOKEN)
dp  = Dispatcher(bot, None, workers=0)

async def cmd_webapp(update: Update, ctx):
    kb = [[InlineKeyboardButton("Заполнить форму", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text("Открыть форму:", reply_markup=InlineKeyboardMarkup(kb))

async def on_webapp_data(update: Update, ctx):
    data = json.loads(update.message.web_app_data.data)
    await update.message.reply_text(f"✅ Получили: {data}")

dp.add_handler(CommandHandler("webapp", cmd_webapp))
dp.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data))

app = Flask(__name__)

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    upd = Update.de_json(request.get_json(force=True), bot)
    dp.process_update(upd)
    return "OK"

if __name__ == "__main__":
    # Устанавливаем webhook у Telegran
    bot.set_webhook(f"https://{HOSTNAME}/{TOKEN}")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
