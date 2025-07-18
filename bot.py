import os
import json
import logging
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from datetime import datetime, timedelta

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Чтение переменных окружения
TOKEN       = os.environ["TELEGRAM_TOKEN"]
SHEET_ID    = os.environ["SHEET_ID"]
CREDS_JSON  = os.environ["GSPREAD_CREDENTIALS_JSON"]

# Авторизация в Google Sheets
creds_dict = json.loads(CREDS_JSON)
scopes     = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds      = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc         = gspread.authorize(creds)
sheet      = gc.open_by_key(SHEET_ID)
experts_ws = sheet.worksheet("Эксперты")
try:
    bookings_ws = sheet.worksheet("Заявки")
except gspread.exceptions.WorksheetNotFound:
    bookings_ws = sheet.add_worksheet("Заявки", rows="1000", cols="5")

# Шаги ConversationHandler
(
    STEP_CHOOSE,
    STEP_REG_NAME, STEP_REG_CITY, STEP_REG_SPHERE, STEP_REG_DESC, STEP_REG_PHOTO,
    STEP_BOOK_NAME, STEP_BOOK_SELECT, STEP_BOOK_DATE, STEP_BOOK_TIME, STEP_BOOK_NOTE
) = range(11)

def load_experts():
    rows = experts_ws.get_all_records()
    return rows

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📋 Консультации", callback_data="act_consult")],
        [InlineKeyboardButton("✏️ Регистрация эксперта", callback_data="act_register")],
    ]
    await update.message.reply_text("Выберите действие:", reply_markup=InlineKeyboardMarkup(kb))
    return STEP_CHOOSE

async def choose_action(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "act_consult":
        await q.message.reply_text("Введите ваше ФИО для записи:")
        return STEP_BOOK_NAME
    else:
        await q.message.reply_text("Регистрация эксперта: введите ваше ФИО:")
        return STEP_REG_NAME

# Регистрация эксперта
async def reg_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["reg_fio"] = update.message.text.strip()
    await update.message.reply_text("Укажите город:")
    return STEP_REG_CITY

async def reg_city(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["reg_city"] = update.message.text.strip()
    await update.message.reply_text("Укажите сферу:")
    return STEP_REG_SPHERE

async def reg_sphere(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["reg_sphere"] = update.message.text.strip()
    await update.message.reply_text("Краткое описание вас как эксперта:")
    return STEP_REG_DESC

async def reg_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["reg_desc"] = update.message.text.strip()
    await update.message.reply_text("Пришлите фото (сертификат или портрет):")
    return STEP_REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    file_id = update.message.photo[-1].file_id if update.message.photo else ""
    experts_ws.append_row([
        ctx.user_data["reg_fio"],
        ctx.user_data["reg_city"],
        ctx.user_data["reg_sphere"],
        ctx.user_data["reg_desc"],
        file_id
    ])
    await update.message.reply_text("✅ Вы зарегистрированы как эксперт!")
    return ConversationHandler.END

# Запись на консультацию
async def book_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["book_name"] = update.message.text.strip()
    experts = load_experts()
    kb = [[InlineKeyboardButton(e["ФИО"], callback_data=f"bk_{i}")] for i,e in enumerate(experts)]
    await update.message.reply_text("Выберите эксперта:", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data["experts_list"] = experts
    return STEP_BOOK_SELECT

async def book_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split("_")[1])
    spec = ctx.user_data["experts_list"][idx]
    ctx.user_data["book_spec"] = spec
    caption = f"{spec['ФИО']}\n{spec['описание']}"
    if spec.get("photo_file_id"):
        await q.message.reply_photo(photo=spec["photo_file_id"], caption=caption)
    else:
        await q.message.reply_text(caption)
    # даты на неделю вперед
    dates = [(datetime.now()+timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    kb = [[InlineKeyboardButton(d, callback_data=f"bd_{d}")] for d in dates]
    await q.message.reply_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(kb))
    return STEP_BOOK_DATE

async def book_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    date = q.data.split("_",1)[1]
    ctx.user_data["book_date"] = date
    times = ["09:00","12:00","15:00","18:00"]
    kb = [[InlineKeyboardButton(t, callback_data=f"bt_{t}")] for t in times]
    await q.message.reply_text("Выберите время:", reply_markup=InlineKeyboardMarkup(kb))
    return STEP_BOOK_TIME

async def book_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    time = q.data.split("_",1)[1]
    ctx.user_data["book_time"] = time
    await q.message.reply_text("Оставьте заметку или вопрос для эксперта:")
    return STEP_BOOK_NOTE

async def book_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    note = update.message.text.strip()
    data = [
        datetime.now().isoformat(),
        ctx.user_data["book_name"],
        ctx.user_data["book_spec"]["ФИО"],
        ctx.user_data["book_date"],
        ctx.user_data["book_time"],
        note
    ]
    bookings_ws.append_row(data)
    await update.message.reply_text("✅ Вы записаны на консультацию!")
    return ConversationHandler.END

# Fallback
fallbacks = [CommandHandler("start", start_cmd)]

conv = ConversationHandler(
    entry_points=[CommandHandler("start", start_cmd)],
    states={
        STEP_CHOOSE:         [CallbackQueryHandler(choose_action, pattern="^act_")],
        STEP_REG_NAME:       [MessageHandler(filters.TEXT, reg_name)],
        STEP_REG_CITY:       [MessageHandler(filters.TEXT, reg_city)],
        STEP_REG_SPHERE:     [MessageHandler(filters.TEXT, reg_sphere)],
        STEP_REG_DESC:       [MessageHandler(filters.TEXT, reg_desc)],
        STEP_REG_PHOTO:      [MessageHandler(filters.PHOTO, reg_photo)],

        STEP_BOOK_NAME:      [MessageHandler(filters.TEXT, book_name)],
        STEP_BOOK_SELECT:    [CallbackQueryHandler(book_select, pattern="^bk_")],
        STEP_BOOK_DATE:      [CallbackQueryHandler(book_date, pattern="^bd_")],
        STEP_BOOK_TIME:      [CallbackQueryHandler(book_time, pattern="^bt_")],
        STEP_BOOK_NOTE:      [MessageHandler(filters.TEXT, book_note)],
    },
    fallbacks=fallbacks,
    per_message=False
)

application = ApplicationBuilder().token(TOKEN).build()

# Отключаем вебхук перед запуском polling (убираем конфликт)
application.bot.delete_webhook(drop_pending_updates=True)

# Регистрируем хендлеры и т.д.
application.add_handler(conv)

# Запуск
if __name__ == "__main__":
    application.run_polling()
