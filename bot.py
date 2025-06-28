import os, json, logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["TELEGRAM_TOKEN"]

async def cmd_webapp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[
      InlineKeyboardButton(
        "Заполнить форму",
        web_app=WebAppInfo(url="https://telegram-kons.vercel.app/")
      )
    ]]
    await update.message.reply_text(
      "Нажми, чтобы открыть форму:",
      reply_markup=InlineKeyboardMarkup(kb)
    )

async def on_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = json.loads(update.message.web_app_data.data)
    fio  = data.get("fio", "")
    city = data.get("city", "")
    await update.message.reply_text(f"✅ Получили: ФИО={fio}, Город={city}")

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("webapp", cmd_webapp))
    app.add_handler(
      MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data)
    )
    # сброс всех накопившихся апдейтов, чтобы не получать старые
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
