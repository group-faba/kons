import os
import json
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes, filters
)

# Логирование
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ['TELEGRAM_TOKEN']
SHEET_ID = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT = int(os.environ.get('PORT', '8080'))

# Google Sheets подключение
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SHEET_ID)

def get_specialists():
    """Чтение специалистов из всех вкладок, кроме 'Лист1'"""
    specs = []
    for ws in spreadsheet.worksheets():
        if ws.title == 'Лист1':
            continue
        data = ws.get_all_records()
        if data:
            d = data[0]
            d['sheet_name'] = ws.title
            specs.append(d)
    return specs

# Flask healthcheck
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# Conversation для /register
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

# Conversation для /start (выбор специалиста)
CHOOSING_SPEC, SHOW_SPEC = range(2)

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
    # Проверяем sheet_name: только строка, никаких цифр и пустоты!
    if not isinstance(spec_sheet, str) or not spec_sheet:
        await update.callback_query.edit_message_text("Ошибка. Попробуйте ещё раз через /start.")
        return ConversationHandler.END
    try:
        ws = spreadsheet.worksheet(spec_sheet)
        rows = ws.get_all_records()
        if not rows:
            await update.callback_query.edit_message_text("Данные не найдены.")
            return ConversationHandler.END
        spec = rows[0]
        text = f"{spec['ФИО']}\n{spec['Описание']}"
        kb = [[InlineKeyboardButton("Назад", callback_data='back')]]
        if spec.get('photo_file_id'):
            await update.callback_query.message.reply_photo(
                photo=spec['photo_file_id'],
                caption=text,
                reply_markup=InlineKeyboardMarkup(kb)
            )
            try:
                await update.callback_query.delete_message()
            except Exception:
                pass
        else:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        ctx.user_data['last_menu'] = True
        return SHOW_SPEC
    except Exception as e:
        await update.callback_query.edit_message_text("Ошибка доступа к анкете.")
        return ConversationHandler.END

async def cb_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    specs = get_specialists()
    kb = [
        [InlineKeyboardButton(f"{spec['ФИО']} / {spec['Город']}", callback_data=spec['sheet_name'])]
        for spec in specs
    ]
    await update.callback_query.message.reply_text("Выберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))
    try:
        await update.callback_query.delete_message()
    except Exception:
        pass
    return CHOOSING_SPEC

# Application и Handlers
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
        SHOW_SPEC: [CallbackQueryHandler(cb_back, pattern='^back$')],
    },
    fallbacks=[],
)

application.add_handler(conv_reg)
application.add_handler(conv_main)

# Запуск
if __name__ == "__main__":
    import threading
    threading.Thread(target=app.run, kwargs={"host":"0.0.0.0", "port":PORT}, daemon=True).start()
    application.run_polling()
