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
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes
)

# Логирование
logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT       = int(os.environ.get('PORT', '8080'))

# Google Sheets
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SHEET_ID)
ws = spreadsheet.worksheet('Лист1')

def get_main_records():
    # Получить всех специалистов (все строки, кроме первой)
    return ws.get_all_records()

# Flask для healthcheck
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# Conversation этапы
CHOOSING_REGION, CHOOSING_FIELD, CHOOSING_SPEC, SHOW_SPEC = range(4)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    records = get_main_records()
    regions = sorted(set(r['Город'] for r in records))
    kb = [[InlineKeyboardButton(region, callback_data=f"region_{i}")]
          for i, region in enumerate(regions)]
    ctx.user_data['regions'] = regions
    ctx.user_data['records'] = records
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    idx = int(update.callback_query.data.split('_')[1])
    region = ctx.user_data['regions'][idx]
    ctx.user_data['selected_region'] = region
    # Фильтруем по региону
    records = ctx.user_data['records']
    fields = sorted(set(r['Сфера'] for r in records if r['Город'] == region))
    kb = [[InlineKeyboardButton(field, callback_data=f"field_{i}")]
          for i, field in enumerate(fields)]
    ctx.user_data['fields'] = fields
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nВыберите сферу:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    idx = int(update.callback_query.data.split('_')[1])
    field = ctx.user_data['fields'][idx]
    ctx.user_data['selected_field'] = field
    # Фильтруем специалистов
    region = ctx.user_data['selected_region']
    records = ctx.user_data['records']
    specs = [r for r in records if r['Город'] == region and r['Сфера'] == field]
    ctx.user_data['specs'] = specs
    kb = [[InlineKeyboardButton(s['ФИО'], callback_data=f"spec_{i}")]
          for i, s in enumerate(specs)]
    await update.callback_query.edit_message_text(
        f"Сфера: {field}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    idx = int(update.callback_query.data.split('_')[1])
    spec = ctx.user_data['specs'][idx]
    ctx.user_data['selected_spec'] = spec
    kb = [
        [InlineKeyboardButton("Назад", callback_data="back")],
        [InlineKeyboardButton("Выбрать этого специалиста", callback_data="choose")]
    ]
    # Показываем карточку
    text = f"{spec['ФИО']}\n{spec['Описание']}"
    if spec.get('photo_file_id'):
        await update.callback_query.message.reply_photo(
            photo=spec['photo_file_id'],
            caption=text,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        await update.callback_query.delete_message()
    else:
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(kb)
        )
    return SHOW_SPEC

async def cb_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    # Вернуться к списку специалистов
    specs = ctx.user_data['specs']
    kb = [[InlineKeyboardButton(s['ФИО'], callback_data=f"spec_{i}")]
          for i, s in enumerate(specs)]
    field = ctx.user_data['selected_field']
    await update.callback_query.message.reply_text(
        f"Сфера: {field}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    await update.callback_query.delete_message()
    return CHOOSING_SPEC

async def cb_choose(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    spec = ctx.user_data['selected_spec']
    await update.callback_query.message.reply_text(
        f"Вы записались к специалисту: {spec['ФИО']}"
    )
    await update.callback_query.delete_message()
    return ConversationHandler.END

# Application и Handlers
application = ApplicationBuilder().token(TOKEN).build()

conv = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CHOOSING_REGION: [CallbackQueryHandler(cb_region, pattern=r"^region_\d+$")],
        CHOOSING_FIELD:  [CallbackQueryHandler(cb_field, pattern=r"^field_\d+$")],
        CHOOSING_SPEC:   [CallbackQueryHandler(cb_spec, pattern=r"^spec_\d+$")],
        SHOW_SPEC: [
            CallbackQueryHandler(cb_back, pattern="^back$"),
            CallbackQueryHandler(cb_choose, pattern="^choose$")
        ],
    },
    fallbacks=[],
)
application.add_handler(conv)

# Запуск
if __name__ == "__main__":
    import threading
    threading.Thread(target=app.run, kwargs={'host':'0.0.0.0', 'port':PORT}, daemon=True).start()
    application.run_polling()
