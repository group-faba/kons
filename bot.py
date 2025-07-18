import os
import json
import logging
import gspread
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO)

TOKEN      = os.environ['TELEGRAM_TOKEN']
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])

# Google Sheets подключение (через google-auth)
import google.auth
from google.oauth2.service_account import Credentials
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_info(CREDS_JSON, scopes=SCOPES)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SHEET_ID)

def get_specialists():
    ws = spreadsheet.worksheet('Эксперты')
    records = ws.get_all_records()
    specialists = []
    for i, row in enumerate(records, 2):  # со 2-й строки
        spec = dict(row)
        spec['row_num'] = i
        slots = []
        slots_str = ws.cell(i, 7).value  # 7 - колонка G (Slots)
        if slots_str:
            for el in slots_str.split(';'):
                el = el.strip()
                if el and ' ' in el:
                    slots.append(el)
        spec['slots'] = slots
        specialists.append(spec)
    return specialists

def get_specialist_row(telegram_id):
    ws = spreadsheet.worksheet('Эксперты')
    records = ws.get_all_records()
    for i, row in enumerate(records, 2):
        if str(row.get('Telegram ID')) == str(telegram_id):
            return ws, i, row
    return None, None, None

def add_slots_for_specialist(telegram_id, date, times):
    ws, row_num, _ = get_specialist_row(telegram_id)
    if not row_num:
        return False
    cur_slots = ws.cell(row_num, 7).value or ''
    cur_list = [s.strip() for s in cur_slots.split(';') if s.strip()]
    for t in times:
        slot = f"{date} {t}"
        if slot not in cur_list:
            cur_list.append(slot)
    ws.update_cell(row_num, 7, ';'.join(sorted(cur_list)))
    return True

# --- СТАРТ, две кнопки ---
CHOOSING, REG_NAME, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO = range(6)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Нужна консультация", callback_data="need_consult")],
        [InlineKeyboardButton("Зарегистрироваться как эксперт", callback_data="register_expert")],
    ]
    await update.message.reply_text(
        "Добро пожаловать! Что вы хотите сделать?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING

async def cb_need_consult(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    specialists = get_specialists()
    regions = sorted(set([spec['Город'] for spec in specialists]))
    kb = [[InlineKeyboardButton(r, callback_data=f'region_{r}')] for r in regions]
    await update.callback_query.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['specialists'] = specialists
    return REG_NAME  # Переход на следующий шаг (или создайте свой)

async def cb_register_expert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Введите ваше ФИО:")
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
    await update.message.reply_text("Пришлите фото сертификата или любой документ:")
    return REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    file_id = ''
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    ws = spreadsheet.worksheet('Эксперты')
    ws.append_row([
        ctx.user_data['fio'],
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        file_id,
        update.effective_user.id,
        update.effective_user.username or '',
        ""  # Слоты
    ])
    await update.message.reply_text("Спасибо, вы зарегистрированы как специалист!")
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

application = ApplicationBuilder().token(TOKEN).build()

conv = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        CHOOSING: [
            CallbackQueryHandler(cb_need_consult, pattern="^need_consult$"),
            CallbackQueryHandler(cb_register_expert, pattern="^register_expert$"),
        ],
        REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
        REG_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)],
        REG_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field)],
        REG_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_PHOTO: [MessageHandler(filters.PHOTO, reg_photo)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

application.add_handler(conv)

if __name__ == "__main__":
    application.run_polling()
