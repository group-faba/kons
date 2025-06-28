import os
import logging
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ['TELEGRAM_TOKEN']
bot = Bot(token=TOKEN)

# Flask
app = Flask(__name__)

# Telegram dispatcher
dp = Dispatcher(bot, None, workers=0, use_context=True)

# /start просто для проверки
def start(update, context):
    update.message.reply_text("Бот запущен через webhook!")

dp.add_handler(CommandHandler("start", start))

@app.route('/', methods=['GET'])
def health():
    return "OK", 200

@app.route(f"/{TOKEN}", methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, bot)
    dp.process_update(update)
    return "OK", 200

if __name__ == "__main__":
    # Устанавливаем webhook (один раз, можно через BotFather)
    bot.set_webhook(f"https://<your-render-url>/{TOKEN}")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
