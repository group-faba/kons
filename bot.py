import os
import json
import logging
import gspread
from flask import Flask, request, jsonify
from google.oauth2.service_account import Credentials
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
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

# ——————————————————————
#     Настройка логирования
# ——————————————————————
logging.basicConfig(level=logging.INFO)

# ——————————————————————
#    Чтение ENV-переменных
# ——————————————————————
TOKEN = os.environ["TELEGRAM_TOKEN"]
SHEET_ID = os.environ["SHEET_ID"]
CREDS_JSON = os.environ.get("GSPREAD_CREDENTIALS_JSON")
if not CREDS_JSON:
    raise RuntimeError("Отсутствует GSPREAD_CREDENTIALS_JSON")
creds_dict = json.loads(CREDS_JSON)

# ——————————————————————
#    Google Sheets авторизация
# ——————————————————————
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID)

# Листы
experts_ws = sheet.worksheet("Эксперты")
# Рабочий лист «Заявки» — в нём фиксируем все брони
try:
    bookings_ws = sheet.worksheet("Заявки")
except gspread.exceptions.WorksheetNotFound:
    bookings_ws = sheet.add_worksheet("Заявки", rows="1000", cols="5")

# ——————————————————————
#    Flask healthcheck (для рендера)
# ——————————————————————
app = Flask(__name__)
@app.route("/", methods=["GET", "HEAD"])
def health():
    return "OK", 200

# ——————————————————————
#      Телеграм-бот
# ——————————————————————
# Состояния ConversationHandler для записи
(
    CHOOSING_ACTION,
    REG_NAME, REG_CITY, REG_SPHERE, REG_DESC, REG_PHOTO,
    BOOK_NAME, BOOK_SELECT_EXPERT, BOOK_DATE, BOOK_TIME, BOOK_QUESTION,
) = range(11)

# Хелперы для работы с таблицей
def load_experts():
    """Возвращает список экспертов из Google Sheets."""
    rows = experts_ws.get_all_records()
    experts = []
    for r in rows:
        experts.append({
            # ключи берём из заголовков столбцов:
            "fio":      r.get("ФИО эксперта", ""),
            "city":     r.get("город эксперта", ""),
            "sphere":   r.get("сфера", ""),
            "desc":     r.get("описание", ""),
            "photo_id": r.get("photo_file_id", ""),  # сюда сохраняем file_id из Telegram
        })
    return experts

# /start — первый шаг
async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📋 Консультации", callback_data="action_consult")],
        [InlineKeyboardButton("✏️ Регистрация эксперта", callback_data="action_register")],
    ]
    await update.message.reply_text("Выберите действие:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_ACTION

# Обработчик кнопок после /start
async def action_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "action_consult":
        # Начинаем flow записи на консультацию
        await q.message.reply_text("Введите ваше ФИО для записи:")
        return BOOK_NAME

    else:  # регистрируем эксперта
        await q.message.reply_text("Регистрация эксперта — введите ваше ФИО:")
        return REG_NAME

# ——— Регистрация эксперта ——————————————————————
async def reg_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["reg_fio"] = update.message.text.strip()
    await update.message.reply_text("Укажите город:")
    return REG_CITY

async def reg_city(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["reg_city"] = update.message.text.strip()
    await update.message.reply_text("Укажите сферу:")
    return REG_SPHERE

async def reg_sphere(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["reg_sphere"] = update.message.text.strip()
    await update.message.reply_text("Кратко о себе:")
    return REG_DESC

async def reg_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["reg_desc"] = update.message.text.strip()
    await update.message.reply_text("Пришлите фото (сертификат или портрет):")
    return REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Сохраняем последний file_id
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    else:
        file_id = ""
    experts_ws.append_row([
        ctx.user_data["reg_fio"],
        ctx.user_data["reg_city"],
        ctx.user_data["reg_sphere"],
        ctx.user_data["reg_desc"],
        file_id,
    ])
    await update.message.reply_text("✅ Вы зарегистрированы как эксперт!")
    return ConversationHandler.END

# ——— Запись на консультацию —————————————————————————————————————
async def book_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["book_name"] = update.message.text.strip()
    experts = load_experts()
    # Группируем по ФИО для кнопок
    kb = [
        [InlineKeyboardButton(e["fio"], callback_data=f"book_expert_{i}")]
        for i,e in enumerate(experts)
    ]
    await update.message.reply_text("Выберите эксперта:", reply_markup=InlineKeyboardMarkup(kb))
    return BOOK_SELECT_EXPERT

async def book_select_expert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split("_")[-1])
    experts = load_experts()
    spec = experts[idx]
    ctx.user_data["book_expert"] = spec

    # Отправляем фото + описание
    caption = f"{spec['fio']}\n{spec['desc']}"
    if spec["photo_id"]:
        await q.message.reply_photo(photo=spec["photo_id"], caption=caption)
    else:
        await q.message.reply_text(caption)

    # Предлагаем выбрать дату (следующие 7 дней)
    dates = [(datetime.now()+timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    kb = [[InlineKeyboardButton(d, callback_data=f"book_date_{d}")] for d in dates]
    await q.message.reply_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(kb))
    return BOOK_DATE

async def book_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    date = q.data.split("_")[-1]
    ctx.user_data["book_date"] = date

    # Горизонтальная кнопка времени (например: 09:00,12:00,15:00)
    times = ["09:00","12:00","15:00"]
    kb = [
        InlineKeyboardButton(t, callback_data=f"book_time_{t}")
        for t in times
    ]
    await q.message.reply_text("Выберите время:", reply_markup=InlineKeyboardMarkup([kb]))
    return BOOK_TIME

async def book_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    time = q.data.split("_")[-1]
    ctx.user_data["book_time"] = time

    await q.message.reply_text("Оставьте вопрос для эксперта:")
    return BOOK_QUESTION

async def book_question(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["book_q"] = update.message.text.strip()
    # Сохраняем в таблицу
    bookings_ws.append_row([
        datetime.now().isoformat(),
        ctx.user_data["book_name"],
        ctx.user_data["book_expert"]["fio"],
        ctx.user_data["book_date"],
        ctx.user_data["book_time"],
        ctx.user_data["book_q"],
    ])
    await update.message.reply_text("✅ Вы записаны на консультацию!")
    return ConversationHandler.END

# ——————————————————————
#   ConversationHandler setup
# ——————————————————————
conv = ConversationHandler(
    entry_points=[CommandHandler("start", start_cmd)],
    states={
        CHOOSING_ACTION:    [CallbackQueryHandler(action_handler, pattern="^action_")],

        # регистрация эксперта
        REG_NAME:           [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
        REG_CITY:           [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)],
        REG_SPHERE:         [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_sphere)],
        REG_DESC:           [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_PHOTO:          [MessageHandler(filters.PHOTO, reg_photo)],

        # запись на консультацию
        BOOK_NAME:          [MessageHandler(filters.TEXT & ~filters.COMMAND, book_name)],
        BOOK_SELECT_EXPERT: [CallbackQueryHandler(book_select_expert, pattern="^book_expert_")],
        BOOK_DATE:          [CallbackQueryHandler(book_date, pattern="^book_date_")],
        BOOK_TIME:          [CallbackQueryHandler(book_time, pattern="^book_time_")],
        BOOK_QUESTION:      [MessageHandler(filters.TEXT & ~filters.COMMAND, book_question)],
    },
    fallbacks=[CommandHandler("start", start_cmd)],
    per_message=False,
)

if __name__ == "__main__":
    # Запускаем Flask (health) и бота вместе
    from threading import Thread

    Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))).start()
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(conv)
    application.run_polling()
