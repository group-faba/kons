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
    """Вернуть список всех специалистов из вкладок кроме 'Лист1'."""
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
    """Вернуть уникальные регионы (города) среди всех специалистов."""
    specs = get_specialists()
    return sorted(set(spec['Город'] for spec in specs))

def get_specs_by_region(region):
    """Вернуть специалистов по региону."""
    specs = get_specialists()
    return [spec for spec in specs if spec['Город'] == region]

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
CHOOSING_REGION, CHOOSING_SPEC, SHOW_SPEC = range(3)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    regions = get_regions()
    if not regions:
        await update.message.reply_text("Нет доступных специалистов.")
        return ConversationHandler.END
    kb = [
        [InlineKeyboardButton(region, callback_data=region)]
        for region in regions
    ]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_REGION

async def cb_choose_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data
    specs = get_specs_by_region(region)
    kb = [
        [InlineKeyboardButton(spec['ФИО'], callback_data=spec['sheet_name'][:64])]
        for spec in specs
    ]
    kb.append([InlineKeyboardButton("Назад", callback_data="back_region")])
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    ctx.user_data['region'] = region
    return CHOOSING_SPEC

async def cb_choose_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    sheet_name = update.callback_query.data
    if sheet_name == "back_region":
        return await cmd_start(update, ctx)
    try:
        ws = spreadsheet.worksheet(sheet_name)
        rows = ws.get_all_records()
        if not rows:
            await update.callback_query.edit_message_text("Данные не найдены.")
            return ConversationHandler.END
        spec = rows[0]
        text = f"{spec['ФИО']}\n{spec['Описание']}"
        kb = [[InlineKeyboardButton("Назад", callback_data=f"back_spec_{ctx.user_data['region']}")]]
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
        return SHOW_SPEC
    except Exception as e:
        await update.callback_query.edit_message_text(f"Ошибка: {e}")
        return ConversationHandler.END

async def cb_back_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = ctx.user_data.get('region', '')
    specs = get_specs_by_region(region)
    kb = [
        [InlineKeyboardButton(spec['ФИО'], callback_data=spec['sheet_name'][:64])]
        for spec in specs
    ]
    kb.append([InlineKeyboardButton("Назад", callback_data="back_region")])
    await update.callback_query.message.reply_text(
        f"Регион: {region}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
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
        CHOOSING_REGION: [CallbackQueryHandler(cb_choose_region)],
        CHOOSING_SPEC: [
            CallbackQueryHandler(cb_choose_spec, pattern=r'^[^b].+'),  # всё кроме back
            CallbackQueryHandler(cb_choose_spec, pattern='^back_region$'),
        ],
        SHOW_SPEC: [
            CallbackQueryHandler(cb_back_spec, pattern=r'^back_spec_'),
        ],
    },
    fallbacks=[],
)

application.add_handler(conv_reg)
application.add_handler(conv_main)

# --- Flask run для Render
def run_flask():
    app.run(host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
