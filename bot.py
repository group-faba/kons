import os
import json
import logging
import asyncio

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes
)

# ——— Настройки окружения ———
TOKEN = os.environ['TELEGRAM_TOKEN']
ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', '0'))
APP_URL = os.environ['APP_URL'].rstrip('/')  # https://<your-domain>
PORT = int(os.environ.get('PORT', '8080'))
GSPREAD_JSON = os.environ['GSPREAD_CREDENTIALS_JSON']
SHEET_ID = os.environ['SHEET_ID']

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ——— Подключение к Google Sheets ———
creds_dict = json.loads(GSPREAD_JSON)
scopes = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive'
]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scopes)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID).sheet1
records = sheet.get_all_records()  # [{ 'ФИО': ..., 'Регион': ..., 'Сфера': ..., 'Сертификат': ... }, ...]

# ——— Conversation States ———
CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC = range(3)

# ——— Утилита для уникальных значений ———
def unique_vals(field, filter_by=None):
    seen = set()
    for r in records:
        if filter_by:
            key, val = next(iter(filter_by.items()))
            if r.get(key) != val:
                continue
        v = r.get(field)
        if v and v not in seen:
            seen.add(v)
            yield v

# ——— Инициализация Telegram-приложения ———
app_bot = ApplicationBuilder().token(TOKEN).build()

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    kb = [[InlineKeyboardButton(r, callback_data=r)] for r in unique_vals('Регион')]
    await update.message.reply_text('Выберите регион:', reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_REGION

async def region_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    region = update.callback_query.data
    ctx.user_data['region'] = region
    kb = [[InlineKeyboardButton(i, callback_data=i)] for i in unique_vals('Сфера', {'Регион': region})]
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nВыберите сферу:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOICE_INDUSTRY

async def industry_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    industry = update.callback_query.data
    ctx.user_data['industry'] = industry
    options = [r for r in records if r['Регион'] == ctx.user_data['region'] and r['Сфера'] == industry]
    ctx.user_data['options'] = options
    kb = [[InlineKeyboardButton(opt['ФИО'], callback_data=str(idx))] for idx, opt in enumerate(options)]
    await update.callback_query.edit_message_text(
        f"Сфера: {industry}\nВыберите консультанта:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOICE_SPEC

async def spec_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    idx = int(update.callback_query.data)
    spec = ctx.user_data['options'][idx]
    text = (
        f"Вы выбрали: {spec['ФИО']}\n"
        f"Регион: {ctx.user_data['region']}\n"
        f"Сфера: {ctx.user_data['industry']}"
    )
    cert_url = spec.get('Сертификат')
    chat_id = update.effective_chat.id
    if cert_url:
        await ctx.bot.send_photo(chat_id=chat_id, photo=cert_url, caption=text)
    else:
        await ctx.bot.send_message(chat_id=chat_id, text=text)
    if ADMIN_CHAT_ID:
        user = update.callback_query.from_user
        await ctx.bot.send_message(
            ADMIN_CHAT_ID,
            f"Новая заявка от {user.full_name} (id={user.id}): {spec['ФИО']} — "
            f"{ctx.user_data['region']}/{ctx.user_data['industry']}"
        )
    return ConversationHandler.END

async def cancel_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Отменено.')
    return ConversationHandler.END

conv = ConversationHandler(
    entry_points=[CommandHandler('start', start_cmd)],
    states={
        CHOICE_REGION:   [CallbackQueryHandler(region_cb)],
        CHOICE_INDUSTRY: [CallbackQueryHandler(industry_cb)],
        CHOICE_SPEC:     [CallbackQueryHandler(spec_cb)],
    },
    fallbacks=[CommandHandler('cancel', cancel_cb)],
)
app_bot.add_handler(conv)

# ——— Flask Webhook-сервер ———
app = Flask(__name__)

@app.route('/', methods=['GET'])
def health():
    return 'OK', 200

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, app_bot.bot)
    # синхронно запускаем обработку
    asyncio.run(app_bot.process_update(update))
    return 'OK', 200

# ——— Установка webhook при старте ———
async def sethook():
    await app_bot.bot.delete_webhook(drop_pending_updates=True)
    await app_bot.bot.set_webhook(f"{APP_URL}/webhook")
asyncio.run(sethook())

# ——— Запуск Flask для локальной разработки ———
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT)
