from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
)
import os
import json

TOKEN = os.environ['TELEGRAM_TOKEN']  # или просто = 'твой_токен'

# --- Кнопка для запуска мини-приложения
async def send_webapp_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton(
            "Открыть мини-приложение",
            web_app=WebAppInfo(url="https://telegram-kons.vercel.app/")  # тут твой URL
        )]
    ]
    await update.message.reply_text(
        "Запусти мини-приложение для записи на консультацию:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# --- Обработка данных из WebApp
async def handle_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.message.web_app_data.data
    form = json.loads(data)
    fio = form.get("fio", "")
    city = form.get("city", "")
    await update.message.reply_text(f"Спасибо! Получено: {fio}, {city}")

if __name__ == "__main__":
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler("webapp", send_webapp_button))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    application.run_polling()
