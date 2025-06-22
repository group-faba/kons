import os
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import ApplicationBuilder, CommandHandler

TOKEN = os.environ["TELEGRAM_TOKEN"]
app = Flask(__name__)
bot_app = ApplicationBuilder().token(TOKEN).build()

async def start(update: Update, context):
    await update.message.reply_text("Бот работает!")

bot_app.add_handler(CommandHandler("start", start))

@app.route("/")
def health():
    return "OK", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    bot_app.create_task(bot_app.process_update(update))
    return "ok", 200

app = app  # для gunicorn: 'bot:app'
