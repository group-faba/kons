import os
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.environ['TELEGRAM_TOKEN']

app = Flask(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Привет! Всё работает.')

bot_app = ApplicationBuilder().token(TOKEN).build()
bot_app.add_handler(CommandHandler('start', start))

asyncio.run(bot_app.initialize())

@app.route('/')
def health():
    return 'OK', 200

@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    asyncio.run(bot_app.process_update(update))
    return 'OK', 200

app = app  # для gunicorn
