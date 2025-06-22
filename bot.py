import os
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.environ['TELEGRAM_TOKEN']
APP_URL = os.environ['APP_URL']

app = Flask(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот живой! /start работает.")

# Telegram bot init
bot_app = ApplicationBuilder().token(TOKEN).build()
bot_app.add_handler(CommandHandler('start', start))

@app.route("/", methods=["GET"])
def health(): return "OK", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    bot_app.update_queue.put_nowait(update)
    return "ok", 200

if __name__ == "__main__":
    import threading
    port = int(os.environ.get('PORT', 8080))
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=port), daemon=True).start()
    bot_app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path="webhook",
        webhook_url=APP_URL + "/webhook"
    )
