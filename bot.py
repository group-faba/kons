import os
import json
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["TELEGRAM_TOKEN"]

async def webapp_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Шлём кнопку, которая откроет ваше мини-приложение.
    """
    kb = [
        [
            InlineKeyboardButton(
                "Заполнить форму",
                web_app=WebAppInfo(
                    url="https://telegram-kons.vercel.app"
                )
            )
        ]
    ]
    await update.message.reply_text(
        "Нажми кнопку, чтобы открыть форму:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Принимаем данные из мини-приложения.
    """
    raw = update.message.web_app_data.data
    form = json.loads(raw)
    fio  = form.get("fio", "<нет ФИО>")
    city = form.get("city", "<нет города>")
    await update.message.reply_text(f"Спасибо! Получили: {fio}, город {city}")

async def startup(app):
    # Удаляем любой ранее установленный webhook, чтобы не было conflict
    await app.bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook удалён, начинаем polling")

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # на старте удаляем вебхук
    app.post_init(startup)

    # Хендлеры
    app.add_handler(CommandHandler("webapp", webapp_cmd))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, webapp_data))

    # Запускаем polling
    app.run_polling()

if __name__ == "__main__":
    main()
