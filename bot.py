import os
import json
import gspread
import logging
from google.oauth2.service_account import Credentials
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ConversationHandler, MessageHandler, ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ['TELEGRAM_TOKEN']
SHEET_ID = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])

SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_info(CREDS_JSON, scopes=SCOPES)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID)
ws = sheet.worksheet('Лист1')

CHOOSING_ACTION, CHOOSING_REGION, CHOOSING_FIELD, CHOOSING_SPEC, CHOOSING_DATE, CHOOSING_TIME, REG_NAME, REG_CITY, REG_FIELD, REG_DESC = range(10)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("Нужна консультация", callback_data="need_consult")],
        [InlineKeyboardButton("Зарегистрироваться как эксперт", callback_data="register_expert")]
    ]
    if update.message:
        await update.message.reply_text(
            "Добро пожаловать! Что вы хотите сделать?",
            reply_markup=InlineKeyboardMarkup(kb)
        )
    elif update.callback_query:
        await update.callback_query.message.edit_text(
            "Добро пожаловать! Что вы хотите сделать?",
            reply_markup=InlineKeyboardMarkup(kb)
        )
    return CHOOSING_ACTION

async def cb_need_consult(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    records = ws.get_all_records()
    regions = sorted(set([row['Город'] for row in records if row.get('Город')]))
    kb = [[InlineKeyboardButton(r, callback_data=f"region_{r}")] for r in regions]
    await update.callback_query.message.reply_text(
        "Выберите регион:", reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_REGION

async def cb_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data.split('_', 1)[1]
    records = ws.get_all_records()
    fields = sorted(set([row['сфера'] for row in records if row.get('Город') == region]))
    kb = [[InlineKeyboardButton(f, callback_data=f"field_{f}")] for f in fields]
    await update.callback_query.message.reply_text(
        f"Регион: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_FIELD

async def cb_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = update.callback_query.data.split('_', 1)[1]
    records = ws.get_all_records()
    region = None
    # найти выбранный регион из предыдущего шага, если надо
    kb = []
    for row in records:
        if row.get('сфера') == field:
            kb.append([InlineKeyboardButton(row['ФИО эксперта'], callback_data=f"spec_{row['ФИО эксперта']}")])
    await update.callback_query.message.reply_text(
        f"Сфера: {field}\nВыберите эксперта:", reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_SPEC

async def cb_spec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    expert_name = update.callback_query.data.split('_', 1)[1]
    records = ws.get_all_records()
    desc = ''
    for row in records:
        if row['ФИО эксперта'] == expert_name:
            desc = row.get('описание', '')
    await update.callback_query.message.reply_text(
        f"{expert_name}\n{desc}\nВыберите дату:\n(даты не реализованы для краткости)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back")]])
    )
    return CHOOSING_DATE

async def cb_register_expert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Введите ваше ФИО:")
    return REG_NAME

async def reg_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['fio'] = update.message.text
    await update.message.reply_text("Введите город:")
    return REG_CITY

async def reg_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['city'] = update.message.text
    await update.message.reply_text("Введите сферу:")
    return REG_FIELD

async def reg_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['field'] = update.message.text
    await update.message.reply_text("Опишите себя коротко:")
    return REG_DESC

async def reg_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['desc'] = update.message.text
    # Сохраняем в таблицу
    ws.append_row([
        context.user_data['fio'],
        context.user_data['city'],
        context.user_data['field'],
        context.user_data['desc'],
        '', '', '', ''  # остальные столбцы пустые для примера
    ])
    await update.message.reply_text("✅ Вы зарегистрированы как эксперт!")
    return ConversationHandler.END

conv = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CHOOSING_ACTION: [
            CallbackQueryHandler(cb_need_consult, pattern="^need_consult$"),
            CallbackQueryHandler(cb_register_expert, pattern="^register_expert$")
        ],
        CHOOSING_REGION: [
            CallbackQueryHandler(cb_region, pattern="^region_")
        ],
        CHOOSING_FIELD: [
            CallbackQueryHandler(cb_field, pattern="^field_")
        ],
        CHOOSING_SPEC: [
            CallbackQueryHandler(cb_spec, pattern="^spec_")
        ],
        REG_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)
        ],
        REG_CITY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)
        ],
        REG_FIELD: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field)
        ],
        REG_DESC: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)
        ],
    },
    fallbacks=[],
)

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(conv)
    app.run_polling()
