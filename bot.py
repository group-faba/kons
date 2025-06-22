import os
import json
import logging

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, ConversationHandler,
    ContextTypes
)

# ————— Настройки окружения —————
TOKEN               = os.environ['TELEGRAM_TOKEN']
ADMIN_CHAT_ID       = int(os.environ['ADMIN_CHAT_ID'])
SHEET_ID            = os.environ['SHEET_ID']
GSPREAD_CREDENTIALS = os.environ['GSPREAD_CREDENTIALS_JSON']
APP_URL             = os.environ['APP_URL']       # https://<your-service>.onrender.com
PORT                = int(os.environ.get('PORT', '8080'))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ————— Инициализация Google Sheets —————
creds_dict = json.loads(GSPREAD_CREDENTIALS)
scopes = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive'
]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scopes)
gc    = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID).sheet1
records = sheet.get_all_records()  # Список словарей

# ————— Conversation States —————
CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC = range(3)

# ————— Утилиты —————
def unique_values(field, filters=None):
    seen = set()
    for r in records:
        if filters:
            key, val = next(iter(filters.items()))
            if r.get(key) != val:
                continue
        v = r.get(field)
        if v and v not in seen:
            seen.add(v)
            yield v

# ————— Telegram Application —————
application = ApplicationBuilder().token(TOKEN).build()

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    kb = [[InlineKeyboardButton(r, callback_data=r)] for r in unique_values('Регион')]
    await update.message.reply_text('Выберите регион:', reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    region = update.callback_query.data
    ctx.user_data['region'] = region
    kb = [[InlineKeyboardButton(i, callback_data=i)] for i in unique_values('Сфера', {'Регион': region})]
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nВыберите сферу:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOICE_INDUSTRY

async def cb_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    industry = update.callback_query.data
    ctx.user_data['industry'] = industry
    options = [r for r in records if r['Регион']==ctx.user_data['region'] and r['Сфера']==industry]
    ctx.user_data['options'] = options
    kb = [[InlineKeyboardButton(opt['ФИО'], callback_data=str(idx))]
          for idx, opt in enumerate(options)]
    await update.callback_query.edit_message_text(
        f"Сфера: {industry}\nВыберите консультанта:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    idx = int(update.callback_query.data)
    spec = ctx.user_data['options'][idx]
    kb = [[InlineKeyboardButton('Сертификат', url=spec['Сертификат'])]]
    text = (
        f"Вы выбрали: {spec['ФИО']}\n"
        f"Регион: {ctx.user_data['region']}\n"
        f"Сфера: {ctx.user_data['industry']}"
    )
    await update.callback_query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb))
    user = update.callback_query.from_user
    await application.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=(f"Новая запись от {user.full_name} (id={user.id}): {spec['ФИО']}, "
              f"{ctx.user_data['region']}/{ctx.user_data['industry']}")
    )
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Отменено.')
    return ConversationHandler.END

conv = ConversationHandler(
    entry_points=[CommandHandler('start', start)],
    states={
        CHOICE_REGION:   [CallbackQueryHandler(cb_region)],
        CHOICE_INDUSTRY: [CallbackQueryHandler(cb_industry)],
        CHOICE_SPEC:     [CallbackQueryHandler(cb_spec)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
application.add_handler(conv)

# ————— Flask Webhook Server —————
flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return 'OK', 200

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.process_update(update)
    return 'OK', 200

# Регистрация вебхука сразу при импорте
import asyncio
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(application.bot.delete_webhook(drop_pending_updates=True))
loop.run_until_complete(application.bot.set_webhook(f"{APP_URL}/webhook"))
loop.close()

# Локальный запуск для отладки
if __name__ == '__main__':
    flask_app.run(host='0.0.0.0', port=PORT)
