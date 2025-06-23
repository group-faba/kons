import os
import json
import threading
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, ConversationHandler,
    ContextTypes
)

# ========== Конфигурация ==========
logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
ADMIN_ID   = int(os.environ.get('ADMIN_CHAT_ID', '0'))
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT       = int(os.environ.get('PORT', '8080'))

# ========== Flask для health-check ==========
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

# ========== Google Sheets ==========
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc    = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID).sheet1

def get_rows():
    return sheet.get_all_records()

# ========== Состояния ==========
CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC, CONFIRM_SPEC = range(4)

# ========== Handlers ==========
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    rows = get_rows()
    seen = set()
    buttons = []
    for r in rows:
        if r['Регион'] not in seen:
            seen.add(r['Регион'])
            buttons.append([InlineKeyboardButton(r['Регион'], callback_data=r['Регион'])])
    ctx.user_data.clear()
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
    await update.callback_query.edit_message_text(
        f'Регион: {region}\nВыберите сферу:',
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CHOICE_INDUSTRY

async def cb_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    industry = update.callback_query.data
    ctx.user_data['industry'] = industry
    rows = get_rows()
    specs = [r for r in rows if r['Регион'] == ctx.user_data['region'] and r['Сфера'] == industry]
    ctx.user_data['specs'] = specs
    buttons = [
        [InlineKeyboardButton(s['ФИО'], callback_data=f'spec_{i}')] for i, s in enumerate(specs)
    ]
    await update.callback_query.edit_message_text(
        f'Сфера: {industry}\nВыберите консультанта:',
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    idx = int(update.callback_query.data.replace('spec_', ''))
    ctx.user_data['cur_spec_idx'] = idx
    spec = ctx.user_data['specs'][idx]
    buttons = [
        [InlineKeyboardButton("Назад", callback_data="back")],
        [InlineKeyboardButton("Выбрать этого специалиста", callback_data=f"choose_{idx}")]
    ]
    caption = f"Вы выбрали: {spec['ФИО']}\n\n{spec.get('Описание','')}"
    await ctx.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=spec['Сертификат'],
        caption=caption,
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CONFIRM_SPEC

async def cb_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    # Вернуться к выбору консультантов
    specs = ctx.user_data['specs']
    industry = ctx.user_data['industry']
    buttons = [
        [InlineKeyboardButton(s['ФИО'], callback_data=f'spec_{i}')] for i, s in enumerate(specs)
    ]
    await update.callback_query.edit_message_text(
        f'Сфера: {industry}\nВыберите консультанта:',
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CHOICE_SPEC

async def cb_choose(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    idx = int(update.callback_query.data.replace('choose_', ''))
    spec = ctx.user_data['specs'][idx]
    user = update.effective_user
    # Здесь запись админу
    await ctx.bot.send_message(
        ADMIN_ID,
        f"Новая запись: {user.full_name} (id={user.id}) -> {spec['ФИО']} [{spec['Регион']}/{spec['Сфера']}]"
    )
    await update.callback_query.edit_message_text('Вы записаны на консультацию!')
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Отменено.')
    return ConversationHandler.END

# ========== Telegram Bot ==========
application = ApplicationBuilder().token(TOKEN).build()
conv = ConversationHandler(
    entry_points=[CommandHandler('start', cmd_start)],
    states={
        CHOICE_REGION:   [CallbackQueryHandler(cb_region)],
        CHOICE_INDUSTRY: [CallbackQueryHandler(cb_industry)],
        CHOICE_SPEC: [
            CallbackQueryHandler(cb_spec, pattern=r'^spec_\d+$'),
        ],
        CONFIRM_SPEC: [
            CallbackQueryHandler(cb_back, pattern='^back$'),
            CallbackQueryHandler(cb_choose, pattern=r'^choose_\d+$')
        ]
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
application.add_handler(conv)

# ========== Запуск ==========
if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
