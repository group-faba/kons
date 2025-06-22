import os
import logging
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler

TOKEN = os.environ['TELEGRAM_TOKEN']

app = Flask(__name__)
application = ApplicationBuilder().token(TOKEN).build()

@app.route('/')
def health():
    return 'OK', 200

@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    # Получаем event loop, создаём задачу
    loop = asyncio.get_event_loop()
    loop.create_task(application.process_update(update))
    return 'OK', 200

async def start(update: Update, context):
    await update.message.reply_text('Привет!')

application.add_handler(CommandHandler('start', start))
