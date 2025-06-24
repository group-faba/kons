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

# Flask healthcheck (для Render)
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# === Вспомогательные функции ===
def get_specialists():
    specialists = []
    for ws in spreadsheet.worksheets():
        if ws.title == 'Лист1':
            continue
        records = ws.get_all_records()
        if records:
            specialist = records[0]
            specialist['sheet_name'] = ws.title
            specialists.append(specialist)
    return specialists

def get_regions():
    regions = set()
    for spec in get_specialists():
        regions.add(spec['Город'].strip())
    return sorted(regions)

def get_fields(region):
    fields = set()
    for spec in get_specialists():
        if spec['Город'].strip() == region:
            fields.add(spec['Сфера'].strip())
    return sorted(fields)

def get_specs(region, field):
    specs = []
    for spec in get_specialists():
        if spec['Город'].strip() == region and spec['Сфера'].strip() == field:
            specs.append(spec)
    return specs

# === Регистрация специалиста ===
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
        ws = spreadsheet.add_worksheet(tab_name, rows="20", cols="10")
    except Exception:
        ws = spreadsheet.worksheet(tab_name)
    ws.append_row(["ФИО", "Город", "Сфера", "Описание", "photo_file_id", "Telegram ID", "Username", "Слоты"])
    ws.append_row([
        fio,
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        ctx.user_data['photo_file_id'],
        update.effective_user.id,
        update.effective_user.username or '',
        ""
    ])
    await update.message.reply_text("Спасибо, вы зарегистрированы как специалист!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отмена регистрации.")
    return ConversationHandler.END

# === Добавление слота ===
ADD_SLOT_WAIT = range(1)
async def addslot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ws = None
    for sheet in spreadsheet.worksheets():
        records = sheet.get_all_records()
        if records and str(records[0].get("Telegram ID")) == str(user_id):
            ws = sheet
            break
    if not ws:
        await update.message.reply_text("Ваша анкета не найдена.")
        return ConversationHandler.END
    ctx.user_data['addslot_sheet'] = ws.title
    await update.message.reply_text("Напишите дату и время слота (например: 25.06 15:00)")
    return ADD_SLOT_WAIT

async def addslot_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    slot = update.message.text.strip()
    ws = spreadsheet.worksheet(ctx.user_data['addslot_sheet'])
    values = ws.row_values(2) if ws.row_count >= 2 else []
    if len(values) < 8:
        values += [""] * (8 - len(values))
    slots_str = values[7]
    if slots_str:
        slots = [s for s in slots_str.split(';') if s.strip()]
    else:
        slots = []
    slots.append(slot)
    ws.update_cell(2, 8, ';'.join(slots))
    await update.message.reply_text("Слот добавлен.")
    return ConversationHandler.END

# === Основное меню: регион —> сфера —> специалист ===
CHOICE_REGION, CHOICE_FIELD, CHOICE_SPEC, SHOW_SPEC = range(4)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    regions = get_regions()
    if not regions:
        await update.message.reply_text("Нет доступных специалистов.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(region, callback_data=f"region|{region}")] for region in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data.split('|', 1)[1]
    ctx.user_data['region'] = region
    fields = get_fields(region)
    if not fields:
        await update.callback_query.edit_message_text("Нет специалистов по выбранному региону.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(field, callback_data=f"field|{field}")] for field in fields]
    await update.callback_query.edit_message_text(f"Регион: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = update.callback_query.data.split('|', 1)[1]
    ctx.user_data['field'] = field
    region = ctx.user_data['region']
    specs = get_specs(region, field)
    if not specs:
        await update.callback_query.edit_message_text("Нет специалистов по выбранной сфере.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(spec['ФИО'], callback_data=f"spec|{spec['sheet_name']}")] for spec in specs]
    await update.callback_query.edit_message_text(f"Сфера: {field}\nВыберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    spec_sheet = update.callback_query.data.split('|', 1)[1]
    ws = spreadsheet.worksheet(spec_sheet)
    rows = ws.get_all_records()
    if not rows:
        await update.callback_query.edit_message_text("Данные не найдены.")
        return ConversationHandler.END
    spec = rows[0]
    text = f"{spec['ФИО']}\n{spec['Описание']}"
    kb = [
        [InlineKeyboardButton("Назад", callback_data='back')],
        [InlineKeyboardButton("Выбрать этого специалиста", callback_data=f"choose|{spec_sheet}")]
    ]
    if spec.get('photo_file_id'):
        await update.callback_query.message.reply_photo(
            photo=spec['photo_file_id'],
            caption=text,
            reply_markup=InlineKeyboardMarkup(kb)
