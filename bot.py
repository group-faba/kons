import os
import json
import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# --- Логирование ---
logging.basicConfig(level=logging.INFO)

# --- Читаем токен из env ---
TOKEN = os.environ["TELEGRAM_TOKEN"]


# 1) Команда /webapp: шлём кнопку для открытия WebApp
async def cmd_webapp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [[
        InlineKeyboardButton(
            text="Заполнить форму",
            web_app=WebAppInfo(url="https://telegram-kons.vercel.app")
        )
    ]]
    await update.message.reply_text(
        "Нажми, чтобы открыть форму:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# 2) Обработчик данных из WebApp (Telegram.WebApp.sendData)
async def on_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    payload = update.message.web_app_data.data  # строка JSON
    data = json.loads(payload)
    fio  = data.get("fio", "")
    city = data.get("city", "")
    await update.message.reply_text(f"✅ Спасибо! Получили: {fio}, {city}")


def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # регистрируем оба хендлера
    app.add_handler(CommandHandler("webapp", cmd_webapp))
    app.add_handler(
        MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data)
    )

    # Опция drop_pending_updates=True очистит все «старые» апдейты
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
