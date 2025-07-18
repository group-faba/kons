import os
import json
import logging
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ConversationHandler, MessageHandler, ContextTypes, filters
)

TOKEN      = os.environ['TELEGRAM_TOKEN']
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])

# Google Sheets авторизация
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
creds = Credentials.from_service_account_info(CREDS_JSON, scopes=SCOPES)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID)
ws = sheet.worksheet('Лист1')  # ИМЯ ЛИСТА ДОЛЖНО СОВПАДАТЬ

logging.basicConfig(level=logging.INFO)

REG_FIO, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO = range(5)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Нужна консультация", callback_data='need_consult')],
        [InlineKeyboardButton("Зарегистрироваться как эксперт", callback_data='register')]
    ]
    await update.message.reply_text(
        "Добро пожаловать! Что вы хотите сделать?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'register':
        await query.message.reply_text("Введите ваше ФИО:")
        return REG_FIO
    elif query.data == 'need_consult':
        # Выводим список специалистов из таблицы
        records = ws.get_all_records()
        specs = [(r['ФИО эксперта'], r['описание'], r['photo_file_id']) for r in records if r.get('ФИО эксперта')]
        keyboard = [
            [InlineKeyboardButton(name, callback_data=f"spec_{i}")]
            for i, (name, _, _) in enumerate(specs)
        ]
        await query.message.reply_text("Выберите специалиста:", reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['specs'] = specs
        return "SHOW_SPEC"
    else:
        await query.message.reply_text("Неизвестная команда.")

async def reg_fio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['fio'] = update.message.text
    await update.message.reply_text("Введите город:")
    return REG_CITY

async def reg_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['city'] = update.message.text
    await update.message.reply_text("Введите сферу:")
    return REG_FIELD

async def reg_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['field'] = update.message.text
    await update.message.reply_text("Опишите себя:")
    return REG_DESC

async def reg_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['desc'] = update.message.text
    await update.message.reply_text("Пришлите фото (как изображение, не файл):")
    return REG_PHOTO

async def reg_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo
    if not photo:
        await update.message.reply_text("Пришли фото (как изображение, не файл)!")
        return REG_PHOTO
    file_id = photo[-1].file_id
    context.user_data['photo_file_id'] = file_id
    ws.append_row([
        context.user_data['fio'],
        context.user_data['city'],
        context.user_data['field'],
        context.user_data['desc'],
        file_id,
        str(update.effective_user.id),
        update.effective_user.username or "",
        ""  # Slots
    ])
    await update.message.reply_text("✅ Вы зарегистрированы как эксперт!")
    return ConversationHandler.END

async def show_spec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.replace("spec_", ""))
    spec = context.user_data['specs'][idx]
    name, desc, file_id = spec
    if file_id:
        await query.message.reply_photo(photo=file_id, caption=f"{name}\n{desc}")
    else:
        await query.message.reply_text(f"{name}\n{desc}")

    return ConversationHandler.END

application = ApplicationBuilder().token(TOKEN).build()

conv = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        REG_FIO:   [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_fio)],
        REG_CITY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)],
        REG_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field)],
        REG_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_PHOTO: [MessageHandler(filters.PHOTO, reg_photo)],
        "SHOW_SPEC": [CallbackQueryHandler(show_spec, pattern=r"^spec_")]
    },
    fallbacks=[],
)

application.add_handler(conv)
application.add_handler(CallbackQueryHandler(main_menu_handler, pattern='^(register|need_consult)$'))

if __name__ == "__main__":
    application.run_polling()
