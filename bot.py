import os
import json
import logging
import gspread

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)
TOKEN     = os.getenv('TELEGRAM_TOKEN')
ADMIN_ID  = int(os.getenv('ADMIN_CHAT_ID', '0'))
SHEET_ID  = os.getenv('SHEET_ID')
CREDS_JSON = os.getenv('GSPREAD_CREDENTIALS_JSON')

# --- Авторизация в Google Sheets ---
creds_dict = json.loads(CREDS_JSON)
gc = gspread.service_account_from_dict(creds_dict)
sheet = gc.open_by_key(SHEET_ID).sheet1  # первый лист

# --- Диалоговые состояния ---
CH_REGION, CH_INDUSTRY = range(2)
# подтянем все уникальные регионы/отрасли из таблицы
all_rows = sheet.get_all_records()
REGIONS    = sorted({r['region']   for r in all_rows})
INDUSTRIES = sorted({r['industry'] for r in all_rows})

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    kb = [[InlineKeyboardButton(r, callback_data=r)] for r in REGIONS]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CH_REGION

async def choose_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data['region'] = update.callback_query.data
    kb = [[InlineKeyboardButton(i, callback_data=i)] for i in INDUSTRIES]
    await update.callback_query.edit_message_text(
        f"Регион: {ctx.user_data['region']}\nВыберите отрасль:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CH_INDUSTRY

async def choose_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    region   = ctx.user_data['region']
    industry = update.callback_query.data

    # фильтруем консультантов
    rows = sheet.get_all_records()
    filtered = [
        r for r in rows
        if r['region']==region and r['industry']==industry
    ]
    if not filtered:
        await update.callback_query.edit_message_text("Никто не найден. /start чтобы попробовать снова.")
        return ConversationHandler.END

    text = "Доступные консультанты:\n\n"
    kb = []
    for r in filtered:
        text += f"• {r['name']}\n"
        kb.append([InlineKeyboardButton("Сертификат", url=r['certificate_url'])])
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    # уведомим админа
    user = update.effective_user
    await ctx.bot.send_message(
        ADMIN_ID,
        f"Пользователь {user.full_name} (id={user.id}) выбрал {region} / {industry}"
    )

    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

# --- Регистрируем handlers ---
application = ApplicationBuilder().token(TOKEN).build()
conv = ConversationHandler(
    entry_points=[CommandHandler('start', start)],
    states={
        CH_REGION:    [CallbackQueryHandler(choose_region)],
        CH_INDUSTRY:  [CallbackQueryHandler(choose_industry)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
application.add_handler(conv)

# --- Запуск ---
if __name__ == '__main__':
    application.run_polling()
