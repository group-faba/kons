import os
import json
import logging

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
)

# ——— Конфигурация из ENV ———
TOKEN        = os.environ["TELEGRAM_TOKEN"]
ADMIN_CHAT_ID= int(os.environ["ADMIN_CHAT_ID"])
SHEET_ID     = os.environ["SHEET_ID"]
GSPREAD_JSON = os.environ["GSPREAD_CREDENTIALS_JSON"]
APP_URL      = os.environ["APP_URL"]       # https://<ваш-сервис>.onrender.com
PORT         = int(os.environ.get("PORT","8080"))

logging.basicConfig(level=logging.INFO)

# ——— Инициализация клиента Google Sheets ———
scope = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(GSPREAD_JSON)
gc = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope))
sheet = gc.open_by_key(SHEET_ID).sheet1
records = sheet.get_all_records()  # список dict с ключами "ФИО","Регион","Сфера","Сертификат"

# ——— Состояния диалога ———
CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC = range(3)

# ——— Утилиты для выборок ———
def unique_values(field, filter_: dict=None):
    vals = [r[field] for r in records if not filter_ or all(r[k]==v for k,v in filter_.items())]
    return sorted(set(vals))

# ——— Хендлеры ———
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    regions = unique_values("Регион")
    kb = [[InlineKeyboardButton(r, callback_data=r)] for r in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    region = update.callback_query.data
    ctx.user_data["region"] = region

    industries = unique_values("Сфера", {"Регион": region})
    kb = [[InlineKeyboardButton(i, callback_data=i)] for i in industries]
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOICE_INDUSTRY

async def cb_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    industry = update.callback_query.data
    ctx.user_data["industry"] = industry

    # фильтруем консультантов и сохраняем список
    opts = [r for r in records
            if r["Регион"]==ctx.user_data["region"]
           and r["Сфера"]==industry]
    ctx.user_data["options"] = opts

    kb = [[InlineKeyboardButton(r["ФИО"], callback_data=str(i))]
          for i,r in enumerate(opts)]
    await update.callback_query.edit_message_text(
        f"Сфера: {industry}\nВыберите консультанта:", reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    idx = int(update.callback_query.data)
    rec = ctx.user_data["options"][idx]

    # Подтверждение пользователю
    text = f"Вы выбрали: {rec['ФИО']}\nРегион: {rec['Регион']}\nСфера: {rec['Сфера']}"
    kb = [[InlineKeyboardButton("Сертификат", url=rec["Сертификат"])]]
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    # Уведомление админу
    u = update.callback_query.from_user
    await ctx.bot.send_message(
        ADMIN_CHAT_ID,
        f"Новая заявка от {u.full_name} (id={u.id}):\n"
        f"{rec['ФИО']} — {rec['Регион']}, {rec['Сфера']}"
    )
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

# ——— Сборка приложения ———
application = ApplicationBuilder().token(TOKEN).build()

conv = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        CHOICE_REGION:    [CallbackQueryHandler(cb_region)],
        CHOICE_INDUSTRY:  [CallbackQueryHandler(cb_industry)],
        CHOICE_SPEC:      [CallbackQueryHandler(cb_spec)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)
application.add_handler(conv)

# ——— Запуск Webhook-сервера ———
if __name__ == "__main__":
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="webhook",
        webhook_url=f"{APP_URL}/webhook"
    )
