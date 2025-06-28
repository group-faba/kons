import os
import json
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["TELEGRAM_TOKEN"]

# 1️⃣ По команде /webapp шлём кнопку
async def webapp_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [
            InlineKeyboardButton(
                "Заполнить форму",
                web_app=WebAppInfo(url="https://telegram-kons.vercel.app/"),
            )
        ]
    ]
    await update.message.reply_text(
        "Нажми на кнопку, чтобы открыть мини-приложение:",
        reply_markup=InlineKeyboardMarkup(kb),
    )

# 2️⃣ Обработчик входящих данных из Web App
async def webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Telegram при sendData отправляет скрытое сообщение с полем web_app_data
    raw = update.message.web_app_data.data
    form = json.loads(raw)
    fio = form.get("fio", "")
    city = form.get("city", "")
    await update.message.reply_text(f"Спасибо! Мы получили: {fio}, город {city}")

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    # хендлеры
    app.add_handler(CommandHandler("webapp", webapp_cmd))
    app.add_handler(
        MessageHandler(filters.StatusUpdate.WEB_APP_DATA, webapp_data)
    )
    # запускаем polling
    app.run_polling()

if __name__ == "__main__":
    main()
