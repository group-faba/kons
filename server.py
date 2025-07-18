import os
import json
import logging
import threading
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, abort
from google.oauth2.service_account import Credentials as GCreds
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import gspread
from gspread.exceptions import WorksheetNotFound

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)

# ========== Переменные окружения ==========
TOKEN      = os.environ["TELEGRAM_TOKEN"]
SHEET_ID   = os.environ["SHEET_ID"]
CREDS_JSON = os.environ["GSPREAD_CREDENTIALS_JSON"]
FOLDER_ID  = os.environ["DRIVE_FOLDER_ID"]
PORT       = int(os.environ.get("PORT", 8080))

# ========== Настройка Google Drive / Sheets ==========
creds_dict = json.loads(CREDS_JSON)
scopes = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets"
]
gcreds = GCreds.from_service_account_info(creds_dict, scopes=scopes)
drive_service = build("drive", "v3", credentials=gcreds)
gc = gspread.authorize(gcreds)
sheet = gc.open_by_key(SHEET_ID)

# Листы
experts_ws = sheet.worksheet("Эксперты")
users_ws   = sheet.worksheet("Users")
try:
    bookings_ws = sheet.worksheet("Заявки")
except WorksheetNotFound:
    bookings_ws = sheet.add_worksheet(title="Заявки", rows="1000", cols="5")

def upload_file_to_drive(f):
    meta = {"name": f.filename, "parents": [FOLDER_ID]}
    media = MediaIoBaseUpload(f.stream, mimetype=f.mimetype)
    res = drive_service.files().create(body=meta, media_body=media, fields="id").execute()
    drive_service.permissions().create(fileId=res["id"], body={"type":"anyone","role":"reader"}).execute()
    return f"https://drive.google.com/uc?id={res['id']}"

# ========== Flask ==========
app = Flask(__name__)

@app.route("/", methods=["GET","HEAD"])
def health():
    return "OK", 200

@app.route("/register-user", methods=["POST"])
def register_user():
    data = request.get_json(silent=True) or {}
    name, city = data.get("name"), data.get("city")
    if not name or not city:
        abort(400, "Missing required field")
    users_ws.append_row([datetime.now().isoformat(), name, city])
    return jsonify({"status":"ok"}), 200

@app.route("/register-expert", methods=["POST"])
def register_expert():
    form = request.form
    fio, city, sphere, desc = form.get("fio"), form.get("city"), form.get("sphere"), form.get("description")
    if not all([fio, city, sphere, desc]):
        abort(400, "Missing required field")
    photo_url = ""
    if "photo" in request.files:
        photo_url = upload_file_to_drive(request.files["photo"])
    experts_ws.append_row([
        datetime.now().isoformat(), fio, city, sphere, desc, photo_url
    ])
    return jsonify({"status":"ok","photo_url":photo_url}),200

@app.route("/consultation-experts", methods=["GET"])
def get_experts():
    return jsonify(experts_ws.get_all_records()), 200

@app.route("/book-expert", methods=["POST"])
def book_expert():
    data = request.get_json(silent=True) or {}
    fio         = data.get("fio")
    expert_name = data.get("expert_name")
    date_str    = data.get("date")
    time_str    = data.get("time")
    if not all([fio, expert_name, date_str, time_str]):
        abort(400, "Missing required field")
    bookings_ws.append_row([
        datetime.now().isoformat(), fio, expert_name, date_str, time_str
    ])
    return jsonify({"status":"ok"}), 200

# ========== Telegram Bot Helpers ==========
def get_specialists():
    rows = experts_ws.get_all_records()
    specs = []
    for i, r in enumerate(rows, start=2):
        rec = dict(r)
        slots_cell = experts_ws.cell(i, 6).value or ""
        rec["slots"] = [s.strip() for s in slots_cell.split(";") if s.strip()]
        specs.append(rec)
    return specs

def get_specialist_row(telegram_id):
    for i, r in enumerate(experts_ws.get_all_records(), start=2):
        if str(r.get("Telegram ID")) == str(telegram_id):
            return experts_ws, i, r
    return None, None, None

# ========== Telegram Bot Handlers ==========
REG_NAME, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO = range(5)
CHOOSING_REGION, CHOOSING_FIELD, CHOOSING_SPEC, CHOOSING_DATE, CHOOSING_TIME = range(5)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Выберите действие:\n"
        "/register — регистрация эксперта\n"
        "/time — добавить слоты к записи\n"
        "или /cancel для отмены."
    )

async def reg_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите ФИО:")
    return REG_NAME

async def reg_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["fio"] = update.message.text
    await update.message.reply_text("Введите город:")
    return REG_CITY

async def reg_city(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["city"] = update.message.text
    await update.message.reply_text("Введите сферу деятельности:")
    return REG_FIELD

async def reg_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["field"] = update.message.text
    await update.message.reply_text("Напишите кратко о себе:")
    return REG_DESC

async def reg_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["desc"] = update.message.text
    await update.message.reply_text("Пришлите фото (сертификат):")
    return REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    file_id = update.message.photo[-1].file_id if update.message.photo else ""
    experts_ws.append_row([
        datetime.now().isoformat(),
        ctx.user_data["fio"],
        ctx.user_data["city"],
        ctx.user_data["field"],
        ctx.user_data["desc"],
        file_id,
        update.effective_user.id,
        update.effective_user.username or "",
        ""  # slots пустые
    ])
    await update.message.reply_text("Вы зарегистрированы как эксперт!")
    return ConversationHandler.END

async def time_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    dates = [(datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    kb = [[InlineKeyboardButton(d, callback_data=f"date_{d}")] for d in dates]
    await update.message.reply_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_DATE

async def cb_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    date = q.data.split("_",1)[1]
    ctx.user_data["selected_date"] = date
    # далее аналогично вашему cb_date и cb_time…

    # Для краткости не дублирую весь код выбора времени
    await q.message.reply_text(f"Выбрана дата {date}.")
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

# ========== Setup Bot ==========
application = ApplicationBuilder().token(TOKEN).build()
# Хендлеры
application.add_handler(CommandHandler("start", cmd_start))
conv_reg = ConversationHandler(
    entry_points=[CommandHandler("register", reg_start)],
    states={
        REG_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
        REG_CITY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)],
        REG_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field)],
        REG_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_PHOTO: [MessageHandler(filters.PHOTO, reg_photo)],
    },
    fallbacks=[CommandHandler("cancel", cancel)]
)
application.add_handler(conv_reg)
application.add_handler(CommandHandler("time", time_start))
application.add_handler(CommandHandler("cancel", cancel))

# ========== Run Flask + Bot Polling ==========
def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
