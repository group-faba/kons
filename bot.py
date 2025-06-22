import os
import json
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes
)

# ========== Конфигурация ==========
logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
ADMIN_ID   = int(os.environ.get('ADMIN_CHAT_ID', '0'))
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT       = int(os.environ.get('PORT', '8080'))
APP_URL    = os.environ['APP_URL']

# ========== Google Sheets ==========
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc    = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID).sheet1

def get_rows():
    return sheet.get_all_records()

# ========== Состояния ==========
CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC = range(3)

# ========== Handlers ==========
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    rows = get_rows()
    seen = set()
    buttons = []
    for r in rows:
        if r['Регион'] not in seen:
            seen.add(r['Регион'])
            buttons.append([InlineKeyboardButton(r['Регион'], callback_data=r['Регион'])])
    await update.message.reply_text('Выберите регион:', reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    region = update.callback_query.data
    ctx.user_data['region'] = region
    rows = get_rows()
    seen = set()
    buttons = []
    for r in rows:
        if r['Регион'] == region and r['Сфера'] not in seen:
            seen.add(r['Сфера'])
            buttons.append([InlineKeyboardButton(r['Сфера'], callback_data=r['Сфера'])])
    await update.callback_query.edit_message_text(f'Регион: {region}\nВыберите сферу:',
                                                 reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_INDUSTRY

async def cb_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    industry = update.callback_query.data
    ctx.user_data['industry'] = industry
    rows = get_rows()
    specs = [r for r in rows if r['Регион'] == ctx.user_data['region'] and r['Сфера'] == industry]
    ctx.user_data['specs'] = specs
    buttons = [[InlineKeyboardButton(s['ФИО'], callback_data=str(i))] for i, s in enumerate(specs)]
    await update.callback_query.edit_message_text(f'Сфера: {industry}\nВыберите консультанта:',
                                                 reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    idx = int(update.callback_query.data)
    spec = ctx.user_data['specs'][idx]
    await update.callback_query.edit_message_text(f"Вы выбрали: {spec['ФИО']}")
    url = spec.get('Сертификат')
    if url:
        await ctx.bot.send_photo(chat_id=update.effective_chat.id, photo=url)
    user = update.effective_user
    await ctx.bot.send_message(ADMIN_ID,
        f"Новая запись: {user.full_name} (id={user.id}) -> {spec['ФИО']} [{ctx.user_data['region']}/{spec['Сфера']}]"
    )
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Отменено.')
    return ConversationHandler.END

# ========== Flask и Webhook ==========
app = Flask(__name__)

@app.route('/')
def health():
    return 'OK', 200

@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    # вызываем асинхронно
    import asyncio
    asyncio.run(application.process_update(update))
    return 'ok', 200

# ========== Application ==========
application = ApplicationBuilder().token(TOKEN).build()
conv = ConversationHandler(
    entry_points=[CommandHandler('start', cmd_start)],
    states={
        CHOICE_REGION:   [CallbackQueryHandler(cb_region)],
        CHOICE_INDUSTRY: [CallbackQueryHandler(cb_industry)],
        CHOICE_SPEC:     [CallbackQueryHandler(cb_spec)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
application.add_handler(conv)

# ========== Запуск ==========
if __name__ == '__main__':
    # set webhook
    import requests
    requests.get(f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={APP_URL}/webhook")
    app.run(host='0.0.0.0', port=PORT)
