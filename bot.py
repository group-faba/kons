import os
import json
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["TELEGRAM_TOKEN"]

# ————————————————
# 1) Кнопка для открытия WebApp
async def cmd_webapp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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

# ————————————————
# 2) Приём данных из WebApp
async def on_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.web_app_data.data
    form = json.loads(raw)
    fio  = form.get("fio", "<нет ФИО>")
    city = form.get("city", "<нет города>")
    await update.message.reply_text(f"Спасибо! Получили: {fio}, город {city}")

# ————————————————
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # Регистрация хэндлеров
    app.add_handler(CommandHandler("webapp", cmd_webapp))
    app.add_handler(
        MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data)
    )

    # Запускаем polling и сбрасываем все старые обновления сразу
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
