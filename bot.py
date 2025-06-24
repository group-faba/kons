import os
import json
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ConversationHandler,
    MessageHandler, ContextTypes, filters
)

# --- Логирование
logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT       = int(os.environ.get('PORT', '8080'))

# --- Google Sheets
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID).sheet1

# --- Flask healthcheck
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# --- Conversation for /register
REG_NAME, REG_REGION, REG_FIELD, REG_DESC, REG_CERT = range(5)

async def reg_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите ФИО:")
    return REG_NAME

async def reg_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['fio'] = update.message.text
    await update.message.reply_text("Введите регион:")
    return REG_REGION

async def reg_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['region'] = update.message.text
    await update.message.reply_text("Введите сферу:")
    return REG_FIELD

async def reg_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['field'] = update.message.text
    await update.message.reply_text("Опишите себя в двух словах:")
    return REG_DESC

async def reg_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['desc'] = update.message.text
    await update.message.reply_text("Пришлите ссылку на сертификат или напишите 'нет':")
    return REG_CERT

async def reg_cert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cert = update.message.text
    fio = ctx.user_data['fio']
    region = ctx.user_data['region']
    field = ctx.user_data['field']
    desc = ctx.user_data['desc']
    row = [fio, region, field, desc, cert]
    # Запись в таблицу
    sheet.append_row(row)
    await update.message.reply_text("Вы успешно зарегистрированы как специалист!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отмена регистрации.")
    return ConversationHandler.END

# --- Conversation для /start (как раньше)
CHOICE_REGION, CHOICE_FIELD, CHOICE_SPEC = range(3)

def get_rows():
    rows = sheet.get_all_records()
    return rows

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = get_rows()
    regions = sorted(set(r['Регион'] for r in rows if r['Регион']))
    kb = [[InlineKeyboardButton(reg, callback_data=reg)] for reg in regions]
    await update.message.reply_text('Выберите регион:', reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data
    ctx.user_data['region'] = region
    rows = get_rows()
    fields = sorted(set(r['Сфера'] for r in rows if r['Регион'] == region))
    kb = [[InlineKeyboardButton(f, callback_data=f)] for f in fields]
    await update.callback_query.edit_message_text(f'Регион: {region}\nВыберите сферу:', reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = update.callback_query.data
    ctx.user_data['field'] = field
    rows = get_rows()
    specs = [r for r in rows if r['Регион'] == ctx.user_data['region'] and r['Сфера'] == field]
    ctx.user_data['specs'] = specs
    kb = [[InlineKeyboardButton(s['ФИО'], callback_data=str(i))] for i, s in enumerate(specs)]
    await update.callback_query.edit_message_text(f'Сфера: {field}\nВыберите консультанта:', reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    idx = int(update.callback_query.data)
    spec = ctx.user_data['specs'][idx]
    text = f"Вы выбрали: {spec['ФИО']}\n{spec['Описание']}\nСертификат: {spec['Сертификат']}"
    await update.callback_query.edit_message_text(text)
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Отменено.')
    return ConversationHandler.END

# --- Запуск Telegram Bot
application = ApplicationBuilder().token(TOKEN).build()

conv_reg = ConversationHandler(
    entry_points=[CommandHandler("register", reg_start)],
    states={
        REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
        REG_REGION: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_region)],
        REG_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field)],
        REG_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_CERT: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_cert)],
    },
    fallbacks=[CommandHandler("cancel", reg_cancel)],
)

conv_main = ConversationHandler(
    entry_points=[CommandHandler('start', cmd_start)],
    states={
        CHOICE_REGION: [CallbackQueryHandler(cb_region)],
        CHOICE_FIELD: [CallbackQueryHandler(cb_field)],
        CHOICE_SPEC: [CallbackQueryHandler(cb_spec)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)

application.add_handler(conv_reg)
application.add_handler(conv_main)

if __name__ == "__main__":
    import threading
    threading.Thread(target=app.run, daemon=True).start()
    application.run_polling()
