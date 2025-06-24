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
import threading
from datetime import datetime

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

def get_specialists():
    """Собрать анкеты всех специалистов из вкладок (кроме 'Лист1')"""
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

def get_free_slots(sheet_name):
    """Собрать свободные слоты для выбранного специалиста"""
    ws = spreadsheet.worksheet(sheet_name)
    data = ws.get_all_records()
    slots = []
    for row in data[1:]:  # 0 - сама анкета, дальше идут слоты
        if not row.get('Клиент') and row.get('Дата') and row.get('Время'):
            slots.append({'Дата': row['Дата'], 'Время': row['Время']})
    return slots

# --- Flask healthcheck (на 0.0.0.0!)
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

# --- Conversation для /register (анкета)
REG_NAME, REG_CITY, REG_FIELD, REG_DESC = range(4)
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
    fio = ctx.user_data['fio']
    tab_name = f"{fio}_{update.effective_user.id}"
    try:
        ws = spreadsheet.add_worksheet(tab_name, rows="100", cols="10")
    except Exception:
        ws = spreadsheet.worksheet(tab_name)
    ws.clear()
    ws.append_row(["ФИО", "Город", "Сфера", "Описание", "Telegram ID", "Username"])
    ws.append_row([
        fio,
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        update.effective_user.id,
        update.effective_user.username or ''
    ])
    ws.append_row(["Дата", "Время", "Клиент", "Телеграм ID"])
    await update.message.reply_text(
        "Вы зарегистрированы как специалист. Для добавления слотов консультаций напишите /addslot"
    )
    return ConversationHandler.END

# --- Conversation для /addslot (добавление слота специалистом)
ADD_DATE, ADD_TIME = range(2)
async def addslot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите дату в формате ДД.ММ.ГГГГ:")
    return ADD_DATE

async def addslot_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['date'] = update.message.text
    await update.message.reply_text("Введите время (например 15:00):")
    return ADD_TIME

async def addslot_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    time_str = update.message.text
    fio = update.effective_user.full_name
    tab_name = f"{fio}_{update.effective_user.id}"
    try:
        ws = spreadsheet.worksheet(tab_name)
    except Exception:
        await update.message.reply_text("Сначала зарегистрируйтесь через /register.")
        return ConversationHandler.END
    ws.append_row([ctx.user_data['date'], time_str, "", ""])
    await update.message.reply_text(f"Слот {ctx.user_data['date']} {time_str} добавлен!")
    return ConversationHandler.END

# --- Conversation для /start (запись к специалисту)
CHOOSING_SPEC, CHOOSING_SLOT = range(2)
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    specs = get_specialists()
    if not specs:
        await update.message.reply_text("Нет доступных специалистов.")
        return ConversationHandler.END
    kb = [
        [InlineKeyboardButton(f"{spec['ФИО']} / {spec['Город']}", callback_data=spec['sheet_name'])]
        for spec in specs
    ]
    await update.message.reply_text("Выберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_SPEC

async def cb_choose_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    spec_sheet = update.callback_query.data
    slots = get_free_slots(spec_sheet)
    if not slots:
        await update.callback_query.edit_message_text("У специалиста нет свободных слотов.")
        return ConversationHandler.END
    kb = [
        [InlineKeyboardButton(f"{slot['Дата']} {slot['Время']}", callback_data=f"{spec_sheet}|{slot['Дата']}|{slot['Время']}")]
        for slot in slots
    ]
    await update.callback_query.edit_message_text("Выберите дату и время:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_SLOT

async def cb_choose_slot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data.split('|')
    spec_sheet, date, time = data[0], data[1], data[2]
    ws = spreadsheet.worksheet(spec_sheet)
    rows = ws.get_all_values()
    for idx, row in enumerate(rows):
        if len(row) >= 2 and row[0] == date and row[1] == time and (len(row) < 3 or not row[2]):
            ws.update_cell(idx+1, 3, update.effective_user.full_name)
            ws.update_cell(idx+1, 4, str(update.effective_user.id))
            await update.callback_query.edit_message_text(
                f"Вы записались на консультацию {date} в {time} к специалисту!"
            )
            return ConversationHandler.END
    await update.callback_query.edit_message_text("Слот уже занят.")
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
    },
    fallbacks=[],
)

conv_addslot = ConversationHandler(
    entry_points=[CommandHandler("addslot", addslot_start)],
    states={
        ADD_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addslot_date)],
        ADD_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addslot_time)],
    },
    fallbacks=[],
)

conv_main = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CHOOSING_SPEC: [CallbackQueryHandler(cb_choose_spec)],
        CHOOSING_SLOT: [CallbackQueryHandler(cb_choose_slot)],
    },
    fallbacks=[],
)

application.add_handler(conv_reg)
application.add_handler(conv_addslot)
application.add_handler(conv_main)

# --- Запуск
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
