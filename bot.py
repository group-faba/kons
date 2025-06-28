import os
import json
import logging

from flask import Flask, request
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ['TELEGRAM_TOKEN']
URL   = os.environ.get('APP_URL')  # в Render: добавить SECRET APP_URL=https://kons.onrender.com
PORT  = int(os.environ.get('PORT', '8080'))

bot = Bot(token=TOKEN)
dp  = Dispatcher(bot, None, use_context=True, workers=0)

app = Flask(__name__)

# 1) Кнопка /webapp
async def send_webapp(update, context):
    kb = [
      [ InlineKeyboardButton(
          "Открыть мини-приложение",
          web_app=WebAppInfo(url=f"{URL}/index.html")
        )
      ]
    ]
    await update.message.reply_text(
      "Запусти мини-приложение:",
      reply_markup=InlineKeyboardMarkup(kb)
    )

# 2) Прием WebAppData
async def on_webapp_data(update, context):
    data = update.message.web_app_data.data
    form = json.loads(data)
    fio  = form.get("fio", "")
    city = form.get("city","")
    await update.message.reply_text(f"Спасибо! Получено: {fio}, {city}")

dp.add_handler(CommandHandler("webapp", send_webapp))
dp.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data))

@app.route('/', methods=['GET'])
def health():
    return "OK", 200

@app.route(f"/{TOKEN}", methods=['POST'])
def webhook():
    payload = request.get_json(force=True)
    update  = Update.de_json(payload, bot)
    dp.process_update(update)
    return "ok", 200

if __name__ == "__main__":
    # Устанавливаем webhook (сбрасываем и ставим заново)
    bot.delete_webhook()
    bot.set_webhook(f"{URL}/{TOKEN}")
    app.run(host="0.0.0.0", port=PORT)
