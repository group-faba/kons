# bot.py
import os
import json
import logging

from flask import Flask, request
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
)

# ——— Конфигурация ———
TOKEN       = os.environ['TELEGRAM_TOKEN']
ADMIN_ID    = int(os.environ.get('ADMIN_CHAT_ID', '0'))
APP_URL     = os.environ['APP_URL'].rstrip('/')
PORT        = int(os.environ.get('PORT', '8080'))
# Google Sheets
GSPREAD_JSON = os.environ['GSPREAD_CREDENTIALS_JSON']
SHEET_ID     = os.environ['SHEET_ID']

# Логирование
logging.basicConfig(level=logging.INFO)

# ——— Подключаемся к Google Sheet ———
creds_dict = json.loads(GSPREAD_JSON)
scope = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive',
]
gc = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope))
sheet = gc.open_by_key(SHEET_ID).sheet1
records = sheet.get_all_records()

def unique_vals(column: str):
    return sorted({r[column] for r in records if r.get(column)})

# ——— Flask-приложение ———
app = Flask(__name__)

@app.route('/', methods=['GET'])
def health():
    return 'OK', 200

@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    bot_app.process_update(update)
    return 'OK', 200

# ——— Telegram-приложение ———
bot_app = ApplicationBuilder().token(TOKEN).build()

# Состояния беседы
CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC = range(3)

async def start_cmd(update: Update, ctx):
    kb = [[InlineKeyboardButton(r, callback_data=r)] for r in unique_vals('Регион')]
    await update.message.reply_text('Выберите регион:', reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_REGION

async def region_cb(update: Update, ctx):
    await update.callback_query.answer()
    region = update.callback_query.data
    ctx.user_data['region'] = region

    # формируем кнопки сфер, доступных в этом регионе
    industries = sorted({
        r['Сфера']
        for r in records
        if r['Регион'] == region and r.get('Сфера')
    })
    kb = [[InlineKeyboardButton(i, callback_data=i)] for i in industries]
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nВыберите сферу:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOICE_INDUSTRY

async def industry_cb(update: Update, ctx):
    await update.callback_query.answer()
    industry = update.callback_query.data
    ctx.user_data['industry'] = industry

    # отфильтруем консультантов по региону+сфере
    opts = [
        r for r in records
        if r['Регион'] == ctx.user_data['region']
        and r['Сфера'] == industry
        and r.get('ФИО')
    ]
    ctx.user_data['options'] = opts

    kb = [
        [InlineKeyboardButton(r['ФИО'], callback_data=str(idx))]
        for idx, r in enumerate(opts)
    ]
    await update.callback_query.edit_message_text(
        f"Сфера: {industry}\nВыберите консультанта:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOICE_SPEC

async def spec_cb(update: Update, ctx):
    await update.callback_query.answer()
    idx = int(update.callback_query.data)
    rec = ctx.user_data['options'][idx]
    fio  = rec['ФИО']
    cert = rec.get('Сертификат')

    text = (
        f"Вы выбрали: {fio}\n"
        f"Регион: {ctx.user_data['region']}\n"
        f"Сфера: {ctx.user_data['industry']}"
    )

    chat_id = update.effective_chat.id

    if cert:
        # присылаем картинку сертификата
        await ctx.bot.send_photo(chat_id=chat_id, photo=cert, caption=text)
    else:
        await ctx.bot.send_message(chat_id=chat_id, text=text)

    # уведомляем администратора
    if ADMIN_ID:
        await ctx.bot.send_message(
            ADMIN_ID,
            f"Новая заявка:\n"
            f"{fio}\n"
            f"{ctx.user_data['region']} | {ctx.user_data['industry']}"
        )

    return ConversationHandler.END

# разговорный хендлер
conv = ConversationHandler(
    entry_points=[CommandHandler('start', start_cmd)],
    states={
        CHOICE_REGION:   [CallbackQueryHandler(region_cb)],
        CHOICE_INDUSTRY: [CallbackQueryHandler(industry_cb)],
        CHOICE_SPEC:     [CallbackQueryHandler(spec_cb)],
    },
    fallbacks=[],
)

bot_app.add_handler(conv)

# ——— Точка входа ———
if __name__ == '__main__':
    # ставим webhook один раз
    bot_app.bot.set_webhook(f"{APP_URL}/webhook")
    # запускаем Flask
    app.run(host='0.0.0.0', port=PORT)
