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
rows  = sheet.get_all_records()

# ========== Состояния ==========
CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC, CHOICE_CONFIRM = range(4)

# ========== Handlers ==========
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    # Уникальные регионы
    seen = set(); buttons = []
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
    # Уникальные сферы
    seen = set(); buttons = []
    for r in rows:
        if r['Регион']==region and r['Сфера'] not in seen:
            seen.add(r['Сфера'])
            buttons.append([InlineKeyboardButton(r['Сфера'], callback_data=r['Сфера'])])
    await update.callback_query.edit_message_text(f'Регион: {region}\nВыберите сферу:', reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_INDUSTRY

async def cb_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    industry = update.callback_query.data
    ctx.user_data['industry'] = industry
    # Все консультанты по фильтру
    specs = [r for r in rows if r['Регион']==ctx.user_data['region'] and r['Сфера']==industry]
    ctx.user_data['specs'] = specs
    ctx.user_data['current_spec_idx'] = 0
    return await show_spec(update, ctx)

async def show_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    idx = ctx.user_data.get('current_spec_idx', 0)
    spec = ctx.user_data['specs'][idx]
    name = spec['ФИО']
    desc = spec['Описание']
    photo = spec['Сертификат']
    text = f'Вы выбрали: {name}\n\n{desc}'
    kb = [
        [InlineKeyboardButton('Назад', callback_data='back')],
        [InlineKeyboardButton('Выбрать этого специалиста', callback_data='choose')]
    ]
    # Отправляем картинку и описание как новое сообщение!
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_photo(photo=photo, caption=text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_photo(photo=photo, caption=text, reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    data = update.callback_query.data
    if data == 'back':
        idx = ctx.user_data.get('current_spec_idx', 0)
        if idx > 0:
            ctx.user_data['current_spec_idx'] = idx - 1
        return await show_spec(update, ctx)
    elif data == 'choose':
        idx = ctx.user_data.get('current_spec_idx', 0)
        spec = ctx.user_data['specs'][idx]
        user = update.effective_user
        await update.callback_query.answer()
        await ctx.bot.send_message(
            ADMIN_ID,
            f"Новая запись: {user.full_name} (id={user.id}) -> {spec['ФИО']} [{spec['Регион']}/{spec['Сфера']}]"
        )
        await update.callback_query.message.reply_text('Вы записаны на консультацию!\nМожешь снова нажать /start')
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
        CHOICE_SPEC:     [CallbackQueryHandler(cb_spec)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
# Важно! /start должен быть доступен всегда:
application.add_handler(CommandHandler('start', cmd_start))
application.add_handler(conv)

# ========== Запуск ==========
if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
