import os
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import asyncio

TOKEN = os.environ['TELEGRAM_TOKEN']

app = Flask(__name__)
bot_app = ApplicationBuilder().token(TOKEN).build()

@app.route('/')
def health():
    return 'OK', 200

@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    loop = asyncio.get_event_loop()
    if loop.is_running():
        asyncio.ensure_future(bot_app.process_update(update))
    else:
        loop.run_until_complete(bot_app.process_update(update))
    return 'OK', 200

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Привет! Всё работает.')

bot_app.add_handler(CommandHandler('start', start))

app = app  # для gunicorn bot:app
