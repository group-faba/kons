from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import os, json

TOKEN = os.environ['TELEGRAM_TOKEN']

async def send_webapp_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[
      InlineKeyboardButton(
        "Открыть мини-приложение",
        web_app=WebAppInfo(url="https://telegram-kons.vercel.app/")
      )
    ]]
    await update.message.reply_text(
        "Запусти мини-приложение:", reply_markup=InlineKeyboardMarkup(kb)
    )

async def handle_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.message.web_app_data.data
    form = json.loads(data)
    await update.message.reply_text(
        f"Спасибо! Получено: {form.get('fio')} из города {form.get('city')}"
    )

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("webapp", send_webapp_button))
    app.add_handler(
      MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data)
    )
    app.run_polling()
