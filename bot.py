import os
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler

TOKEN = os.environ["TELEGRAM_TOKEN"]

app = Flask(__name__)

bot_app = ApplicationBuilder().token(TOKEN).build()

async def start(update: Update, context):
    await update.message.reply_text("Привет! Бот работает.")

bot_app.add_handler(CommandHandler("start", start))

@app.route("/")
def health():
    return "OK", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    bot_app.create_task(bot_app.process_update(update))
    return "OK", 200

app = app  # чтобы gunicorn подхватил

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
