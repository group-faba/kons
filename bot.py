import os
import sqlite3
import asyncio
import logging

from flask import Flask, request
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# 1) Конфиг из ENV
TOKEN    = os.getenv('TELEGRAM_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_CHAT_ID', '0'))
APP_URL  = os.getenv('APP_URL')        # https://<ваш-сервис>.onrender.com
DB_PATH  = os.getenv('DB_PATH', 'bot.db')
PORT     = int(os.getenv('PORT', '8080'))

# 2) Логирование
logging.basicConfig(level=logging.INFO)

# 3) Инициализация БД
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
      CREATE TABLE IF NOT EXISTS tokens (
        user_id TEXT PRIMARY KEY,
        token TEXT, refresh_token TEXT,
        token_uri TEXT, client_id TEXT,
        client_secret TEXT, scopes TEXT
      )
    ''')
    conn.commit()
    conn.close()

# 4) Создаём Telegram-приложение
init_db()
application = ApplicationBuilder().token(TOKEN).build()
bot = application.bot

# 5) Ваши хендлеры (пример — только /start)
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я запущен на вебхуках и готов отвечать.")

application.add_handler(CommandHandler('start', cmd_start))


# 6) Flask-приложение для вебхука
app = Flask(__name__)

@app.route('/')
def healthz():
    return 'OK', 200

@app.route('/webhook', methods=['POST'])
def webhook():
    # получаем JSON от Telegram
    data = request.get_json(force=True)
    update = Update.de_json(data, bot)
    # запускаем обработку в фоне
    asyncio.get_event_loop().create_task(application.process_update(update))
    return 'OK', 200


# 7) Точка старта при локальной отладке
if __name__ == '__main__':
    # сбросим старый вебхук, выставим новый
    bot.delete_webhook(drop_pending_updates=True)
    bot.set_webhook(f"{APP_URL}/webhook")
    # запускаем Flask
    app.run(host='0.0.0.0', port=PORT)
