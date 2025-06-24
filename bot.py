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

# --- Логирование и переменные окружения
logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT       = int(os.environ.get('PORT', '8080'))

# --- Google Sheets
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SHEET_ID)

def get_main_records():
    ws = spreadsheet.worksheet('Лист1')
    return ws.get_all_records()

def get_specialists():
    specs = []
    for ws in spreadsheet.worksheets():
        if ws.title == 'Лист1':
            continue
        data = ws.get_all_records()
        if data:
            card = data[0]
            card['sheet_name'] = ws.title
            specs.append(card)
    return specs

# --- Flask healthcheck
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# --- Conversation для /register (анкета)
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
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        ctx.user_data['photo_file_id'] = file_id
    else:
        ctx.user_data['photo_file_id'] = ''
    fio = ctx.user_data['fio']
    tab_name = f"{fio}_{update.effective_user.id}"
    try:
        ws = spreadsheet.add_worksheet(tab_name, rows="10", cols="10")
    except Exception:
        ws = spreadsheet.worksheet(tab_name)
    ws.append_row(["ФИО", "Город", "Сфера", "Описание", "photo_file_id", "Telegram ID", "Username"])
    ws.append_row([
        fio,
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        ctx.user_data['photo_file_id'],
        update.effective_user.id,
        update.effective_user.username or ''
    ])
    await update.message.reply_text("Спасибо, вы зарегистрированы как специалист!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отмена регистрации.")
    return ConversationHandler.END

# --- Conversation для /start (выбор специалиста)
CHOICE_REGION, CHOICE_FIELD, CHOICE_SPEC, SHOW_SPEC = range(4)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    records = get_main_records()
    regions = sorted(set(r['Регион'] for r in records if r.get('Регион')))
    buttons = [[InlineKeyboardButton(region, callback_data=f"region|{region}")] for region in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data.split('|', 1)[1]
    ctx.user_data['region'] = region
    records = get_main_records()
    fields = sorted(set(r['Сфера'] for r in records if r['Регион'] == region and r.get('Сфера')))
    buttons = [[InlineKeyboardButton(field, callback_data=f"field|{field}")] for field in fields]
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CHOICE_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = update.callback_query.data.split('|', 1)[1]
    ctx.user_data['field'] = field
    region = ctx.user_data['region']
    specs = [
        s for s in get_specialists()
        if s.get('Город', '').lower() == region.lower() and s.get('Сфера', '').lower() == field.lower()
    ]
    if not specs:
        await update.callback_query.edit_message_text("Нет специалистов по выбранному региону и сфере.")
        return ConversationHandler.END
    buttons = [
        [InlineKeyboardButton(s['ФИО'], callback_data=f"spec|{s['sheet_name']}")] for s in specs
    ]
    await update.callback_query.edit_message_text(
        f"Сфера: {field}\nВыберите специалиста:", reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    sheet_name = update.callback_query.data.split('|', 1)[1]
    ws = spreadsheet.worksheet(sheet_name)
    rows = ws.get_all_records()
    if not rows:
        await update.callback_query.edit_message_text("Данные не найдены.")
        return ConversationHandler.END
    spec = rows[0]
    text = f"{spec['ФИО']}\n\n{spec['Описание']}"
    kb = [
        [InlineKeyboardButton("Назад", callback_data='back')],
        [InlineKeyboardButton("Выбрать этого специалиста", callback_data=f"choose|{sheet_name}")]
    ]
    if spec.get('photo_file_id'):
        await update.callback_query.message.reply_photo(
            photo=spec['photo_file_id'],
            caption=text,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        await update.callback_query.delete_message()
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['last_sheet'] = sheet_name
    return SHOW_SPEC

async def cb_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = ctx.user_data.get('field')
    region = ctx.user_data.get('region')
    specs = [
        s for s in get_specialists()
        if s.get('Город', '').lower() == region.lower() and s.get('Сфера', '').lower() == field.lower()
    ]
    buttons = [
        [InlineKeyboardButton(s['ФИО'], callback_data=f"spec|{s['sheet_name']}")] for s in specs
    ]
    await update.callback_query.message.reply_text(
        f"Сфера: {field}\nВыберите специалиста:", reply_markup=InlineKeyboardMarkup(buttons)
    )
    await update.callback_query.delete_message()
    return CHOICE_SPEC

async def cb_choose(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("Вы записались на консультацию к специалисту.")
    return ConversationHandler.END

# --- Application и Handlers
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
        CHOICE_REGION: [CallbackQueryHandler(cb_region, pattern=r'^region\|')],
        CHOICE_FIELD:  [CallbackQueryHandler(cb_field,  pattern=r'^field\|')],
        CHOICE_SPEC:   [CallbackQueryHandler(cb_spec,   pattern=r'^spec\|')],
        SHOW_SPEC: [
            CallbackQueryHandler(cb_back, pattern=r'^back$'),
            CallbackQueryHandler(cb_choose, pattern=r'^choose\|')
        ],
    },
    fallbacks=[],
)

application.add_handler(conv_reg)
application.add_handler(conv_main)

# --- Запуск
if __name__ == "__main__":
    import threading
    threading.Thread(target=app.run, kwargs={'host':'0.0.0.0','port':PORT}, daemon=True).start()
    application.run_polling()
