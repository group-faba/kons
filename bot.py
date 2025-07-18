import os
import json
import logging
import gspread
from flask import Flask
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
from datetime import datetime

logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])

# Google Sheets подключение
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
gc = gspread.service_account_from_dict(CREDS_JSON)
sheet = gc.open_by_key(SHEET_ID)
ws = sheet.worksheet('Консультации')

# Conversation states
CHOOSE_ACTION, CHOOSE_SPECIALIST, CHOOSE_DATE = range(3)

# /start
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Нужна консультация", callback_data='need_consult')],
        [InlineKeyboardButton("Зарегистрироваться как эксперт", callback_data='register_expert')],
    ]
    await update.message.reply_text(
        "Добро пожаловать! Что вы хотите сделать?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSE_ACTION

# Обработка кнопки "Нужна консультация"
async def cb_need_consult(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Получаем специалистов
    records = ws.get_all_records()
    # Запоминаем специалистов для дальнейшего использования
    context.user_data['specialists'] = records

    # Формируем клавиатуру по именам специалистов
    kb = []
    filtered_specs = {}
    for spec in records:
        name = spec['ФИО эксперта']
        tid = spec['Telegram ID']
        filtered_specs[str(tid)] = spec
        kb.append([InlineKeyboardButton(name, callback_data=f'spec_{tid}')])

    context.user_data['filtered_specs'] = filtered_specs

    await query.message.reply_text(
        "Выберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSE_SPECIALIST

# Обработка выбора специалиста
async def cb_spec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    spec_id = query.data.split('_', 1)[1]
    spec = context.user_data['filtered_specs'][spec_id]
    context.user_data['selected_specialist'] = spec

    text = f"{spec['ФИО эксперта']}\n{spec['описание']}\nВыберите дату:"
    kb = [
        [InlineKeyboardButton("⬅️ Назад", callback_data='back')]
    ]

    # Показываем фото, если оно есть
    if spec.get('photo_file_id'):
        await query.message.reply_photo(
            photo=spec['photo_file_id'],
            caption=text,
            reply_markup=InlineKeyboardMarkup(kb)
        )
    else:
        await query.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(kb)
        )
    return CHOOSE_DATE

# Обработка кнопки "Назад"
async def cb_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # Возврат к выбору специалиста
    records = context.user_data.get('specialists', [])
    kb = []
    filtered_specs = {}
    for spec in records:
        name = spec['ФИО эксперта']
        tid = spec['Telegram ID']
        filtered_specs[str(tid)] = spec
        kb.append([InlineKeyboardButton(name, callback_data=f'spec_{tid}')])
    context.user_data['filtered_specs'] = filtered_specs
    await query.message.reply_text(
        "Выберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSE_SPECIALIST

# Обработка кнопки "Зарегистрироваться как эксперт" (заглушка)
async def cb_register_expert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Регистрация эксперта — в разработке.")
    return ConversationHandler.END

# ConversationHandler
conv = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CHOOSE_ACTION: [
            CallbackQueryHandler(cb_need_consult, pattern='^need_consult$'),
            CallbackQueryHandler(cb_register_expert, pattern='^register_expert$')
        ],
        CHOOSE_SPECIALIST: [
            CallbackQueryHandler(cb_spec, pattern=r'^spec_')
        ],
        CHOOSE_DATE: [
            CallbackQueryHandler(cb_back, pattern='^back$')
        ]
    },
    fallbacks=[],
)

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(conv)
    app.run_polling()
