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

# --- Flask healthcheck (чтобы Render не засыпал)
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# --- Получение специалистов из всех вкладок
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

# --- Регистрация специалиста /register
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

# --- Добавление слота /addslot
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
    # Добавляем время в первый пустой слот
    values = ws.row_values(2) if ws.row_count >= 2 else []
    if len(values) < 8:
        values += [""] * (8 - len(values))
    slots_str = values[7]
    if slots_str:
        slots = slots_str.split(';')
    else:
        slots = []
    slots.append(slot)
    ws.update_cell(2, 8, ';'.join(slots))
    await update.message.reply_text("Слот добавлен.")
    return ConversationHandler.END

# --- Выбор специалиста /start
CHOOSING_SPEC, SHOW_SPEC, SELECTED_SPEC = range(3)
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    specs = get_specialists()
    if not specs:
        await update.message.reply_text("Нет доступных специалистов.")
        return ConversationHandler.END
    kb = [
        [InlineKeyboardButton(f"{spec['ФИО']}", callback_data=spec['sheet_name'])]
        for spec in specs
    ]
    await update.message.reply_text("Выберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_SPEC

async def cb_choose_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    spec_sheet = update.callback_query.data
    ws = spreadsheet.worksheet(spec_sheet)
    rows = ws.get_all_records()
    if not rows:
        await update.callback_query.edit_message_text("Данные не найдены.")
        return ConversationHandler.END
    spec = rows[0]
    # Собираем слоты
    slots = []
    if 'Слоты' in spec and spec['Слоты']:
        slots = [s for s in spec['Слоты'].split(';') if s.strip()]
    text = f"{spec['ФИО']}\n{spec['Описание']}"
    kb = []
    if slots:
        for slot in slots:
            kb.append([InlineKeyboardButton(slot, callback_data=f"slot_{slot}")])
    kb.append([
        InlineKeyboardButton("Назад", callback_data='back'),
        InlineKeyboardButton("Выбрать этого специалиста", callback_data=f"choose_{spec_sheet}")
    ])
    if spec.get('photo_file_id'):
        await update.callback_query.message.reply_photo(
            photo=spec['photo_file_id'],
            caption=text,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        await update.callback_query.delete_message()
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['last_menu'] = True
    ctx.user_data['last_sheet'] = spec_sheet
    return SHOW_SPEC

async def cb_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    specs = get_specialists()
    kb = [
        [InlineKeyboardButton(f"{spec['ФИО']}", callback_data=spec['sheet_name'])]
        for spec in specs
    ]
    await update.callback_query.message.reply_text("Выберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))
    await update.callback_query.delete_message()
    return CHOOSING_SPEC

async def cb_choose(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Вы записаны на консультацию!")
    await update.callback_query.delete_message()
    return ConversationHandler.END

# --- Telegram Application и Handlers
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
        CHOOSING_SPEC: [CallbackQueryHandler(cb_choose_spec)],
        SHOW_SPEC: [
            CallbackQueryHandler(cb_back, pattern='^back$'),
            CallbackQueryHandler(cb_choose, pattern='^choose_.*$'),
        ],
    },
    fallbacks=[],
)

conv_addslot = ConversationHandler(
    entry_points=[CommandHandler("addslot", addslot_start)],
    states={
        ADD_SLOT_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, addslot_receive)],
    },
    fallbacks=[],
)

application.add_handler(conv_reg)
application.add_handler(conv_main)
application.add_handler(conv_addslot)

# --- Запуск Flask и Telegram polling
if __name__ == "__main__":
    import threading
    threading.Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': PORT}, daemon=True).start()
    application.run_polling()
