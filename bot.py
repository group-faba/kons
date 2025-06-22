import os
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)
import logging
import asyncio

TOKEN = os.environ["TELEGRAM_TOKEN"]

app = Flask(__name__)

bot_app = ApplicationBuilder().token(TOKEN).build()

@app.route("/")
def health():
    return "OK", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    asyncio.get_event_loop().create_task(bot_app.process_update(update))
    return "OK", 200

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет, бот работает!")

bot_app.add_handler(CommandHandler("start", start))

if __name__ == "__main__":
    bot_app.run_polling()
