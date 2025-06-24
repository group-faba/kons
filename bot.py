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
            # Делаем короткий id для callback_data
            if 'Telegram ID' in card and str(card['Telegram ID']).isdigit():
                card['callback_id'] = f"id_{card['Telegram ID']}"
            else:
                card['callback_id'] = ws.title[:60].replace(' ', '_')
            specs.append(card)
    return specs

def get_specialist_by_callback(callback_id):
    """Найти специалиста по короткому id (callback_data)"""
    for ws in spreadsheet.worksheets():
        if ws.title == 'Лист1':
            continue
        data = ws.get_all_records()
        if data:
            card = data[0]
            cid = f"id_{card.get('Telegram ID', '')}"
            if cid == callback_id:
                return ws, card
    return None, None

# --- Flask healthcheck (чтобы Render не засыпал)
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
    # Сохраняем анкету на новую вкладку (sheet)
    fio = ctx.user_data['fio']
    tab_name = f"{fio}_{update.effective_user.id}"
    try:
        ws = spreadsheet.add_worksheet(tab_name, rows="10", cols="10")
    except Exception:
        ws = spreadsheet.worksheet(tab_name)
    ws.clear()
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
CHOOSING_REGION, CHOOSING_SPEC, SHOW_SPEC = range(3)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    specs = get_specialists()
    if not specs:
        await update.message.reply_text("Нет доступных специалистов.")
        return ConversationHandler.END
    # Выбор регионов
    regions = sorted(set(spec['Город'] for spec in specs))
    kb = [
        [InlineKeyboardButton(region, callback_data=f"region_{region}")]
        for region in regions
    ]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_REGION

async def cb_choose_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data.replace("region_", "", 1)
    specs = get_specialists()
    filtered = [s for s in specs if s['Город'] == region]
    if not filtered:
        await update.callback_query.edit_message_text("Нет специалистов в этом регионе.")
        return ConversationHandler.END
    kb = [
        [InlineKeyboardButton(f"{s['ФИО']} / {s['Сфера']}", callback_data=s['callback_id'])]
        for s in filtered
    ]
    await update.callback_query.edit_message_text(
        f"Выберите специалиста в регионе {region}:", reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_SPEC

async def cb_choose_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    callback_id = update.callback_query.data
    ws, spec = get_specialist_by_callback(callback_id)
    if not spec:
        await update.callback_query.edit_message_text("Данные специалиста не найдены.")
        return ConversationHandler.END
    text = f"{spec['ФИО']}\n{spec['Сфера']}\n{spec['Описание']}"
    kb = [[InlineKeyboardButton("Назад", callback_data=f"back_{spec['Город']}")]]
    if spec.get('photo_file_id'):
        await update.callback_query.message.reply_photo(
            photo=spec['photo_file_id'],
            caption=text,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        await update.callback_query.delete_message()
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return SHOW_SPEC

async def cb_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data.replace("back_", "", 1)
    specs = get_specialists()
    filtered = [s for s in specs if s['Город'] == region]
    kb = [
        [InlineKeyboardButton(f"{s['ФИО']} / {s['Сфера']}", callback_data=s['callback_id'])]
        for s in filtered
    ]
    await update.callback_query.message.reply_text(
        f"Выберите специалиста в регионе {region}:", reply_markup=InlineKeyboardMarkup(kb)
    )
    await update.callback_query.delete_message()
    return CHOOSING_SPEC

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
        CHOOSING_REGION: [CallbackQueryHandler(cb_choose_region, pattern=r"^region_")],
        CHOOSING_SPEC: [CallbackQueryHandler(cb_choose_spec, pattern=r"^id_\d+")],
        SHOW_SPEC: [
            CallbackQueryHandler(cb_back, pattern=r"^back_"),
        ],
    },
    fallbacks=[],
)

application.add_handler(conv_reg)
application.add_handler(conv_main)

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

# --- Запуск
if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
