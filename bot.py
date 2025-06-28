import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import json

TOKEN = os.environ.get("TELEGRAM_TOKEN")  # обязательно пропиши токен в переменных окружения

async def send_webapp_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton(
            "Открыть мини-приложение",
            web_app=WebAppInfo(url="https://telegram-kons.vercel.app/")
        )]
    ]
    await update.message.reply_text(
        "Запусти мини-приложение для записи на консультацию:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.message.web_app_data.data
    form = json.loads(data)
    fio = form.get("fio", "")
    city = form.get("city", "")
    await update.message.reply_text(f"Спасибо! Получено: {fio}, {city}")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("webapp", send_webapp_button))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    app.run_polling()
