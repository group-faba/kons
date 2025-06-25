import os
import json
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

# --- Логирование
logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT       = int(os.environ.get('PORT', '8080'))

# --- Google Sheets подключение
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SHEET_ID)
ws = spreadsheet.worksheet('Лист1')

def get_specialists():
    rows = ws.get_all_records()
    return rows

# --- Flask healthcheck
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# --- Регистрация специалиста
REG_NAME, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO = range(5)
async def reg_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите ФИО:")
    return REG_NAME

async def reg_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['fio'] = update.message.text
    await update.message.reply_text("Введите город:")
    return REG_CITY

async def reg_city(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['city'] = update.message.text
    await update.message.reply_text("Введите сферу деятельности:")
    return REG_FIELD

async def reg_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['field'] = update.message.text
    await update.message.reply_text("Напишите кратко о себе:")
    return REG_DESC

async def reg_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['desc'] = update.message.text
    await update.message.reply_text("Пришлите фото сертификата (или любой документ):")
    return REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    file_id = ''
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    ws.append_row([
        ctx.user_data['fio'],
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        file_id,
        update.effective_user.id,
        update.effective_user.username or ''
    ])
    await update.message.reply_text("Спасибо, вы зарегистрированы как специалист!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отмена регистрации.")
    return ConversationHandler.END

# --- Основная логика по выбору специалиста
CHOOSING_REGION, CHOOSING_FIELD, CHOOSING_SPEC, SHOW_SPEC = range(4)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    specs = get_specialists()
    regions = sorted(set(s['Город'] for s in specs if s['Город']))
    kb = [[InlineKeyboardButton(r, callback_data=f"region_{r}")] for r in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_REGION

async def cb_choose_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data.replace('region_', '')
    ctx.user_data['region'] = region
    specs = get_specialists()
    fields = sorted(set(s['Сфера'] for s in specs if s['Город'] == region))
    kb = [[InlineKeyboardButton(f, callback_data=f"field_{f}")] for f in fields]
    await update.callback_query.edit_message_text(f"Регион: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_FIELD

async def cb_choose_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = update.callback_query.data.replace('field_', '')
    ctx.user_data['field'] = field
    specs = get_specialists()
    specialists = [s for s in specs if s['Город'] == ctx.user_data['region'] and s['Сфера'] == field]
    kb = [[InlineKeyboardButton(s['ФИО'], callback_data=f"spec_{i}")]
          for i, s in enumerate(specialists)]
    ctx.user_data['specs'] = specialists
    await update.callback_query.edit_message_text(
        f"Сфера: {field}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_SPEC

async def cb_choose_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    idx = int(update.callback_query.data.replace('spec_', ''))
    spec = ctx.user_data['specs'][idx]
    text = f"{spec['ФИО']}\n{spec['Описание']}"
    kb = [
        [InlineKeyboardButton("Назад", callback_data="back")],
        [InlineKeyboardButton("Выбрать этого специалиста", callback_data="choose_this")]
    ]
    if spec['photo_file_id']:
        await update.callback_query.message.reply_photo(
            photo=spec['photo_file_id'],
            caption=text,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        await update.callback_query.delete_message()
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['selected'] = spec
    return SHOW_SPEC

async def cb_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = ctx.user_data.get('field')
    region = ctx.user_data.get('region')
    specs = get_specialists()
    specialists = [s for s in specs if s['Город'] == region and s['Сфера'] == field]
    kb = [[InlineKeyboardButton(s['ФИО'], callback_data=f"spec_{i}")]
          for i, s in enumerate(specialists)]
    await update.callback_query.message.reply_text(
        f"Сфера: {field}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    await update.callback_query.delete_message()
    return CHOOSING_SPEC

async def cb_choose_this(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    spec = ctx.user_data.get('selected')
    await update.callback_query.message.reply_text(
        f"Вы записались к специалисту: {spec['ФИО']}"
    )
    return ConversationHandler.END

# --- Хендлеры и запуск
application = ApplicationBuilder().token(TOKEN).build()

conv_reg = ConversationHandler(
    entry_points=[CommandHandler("register", reg_start)],
    states={
        REG_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
        REG_CITY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)],
        REG_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field)],
        REG_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_PHOTO: [MessageHandler(filters.PHOTO, reg_photo)],
    },
    fallbacks=[CommandHandler("cancel", reg_cancel)],
)

conv_main = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CHOOSING_REGION: [CallbackQueryHandler(cb_choose_region, pattern='^region_')],
        CHOOSING_FIELD:  [CallbackQueryHandler(cb_choose_field, pattern='^field_')],
        CHOOSING_SPEC:   [CallbackQueryHandler(cb_choose_spec, pattern='^spec_')],
        SHOW_SPEC: [
            CallbackQueryHandler(cb_back, pattern='^back$'),
            CallbackQueryHandler(cb_choose_this, pattern='^choose_this$'),
        ],
    },
    fallbacks=[],
)

application.add_handler(conv_reg)
application.add_handler(conv_main)

if __name__ == "__main__":
    import threading
    threading.Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': PORT}, daemon=True).start()
    application.run_polling()
