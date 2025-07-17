import os
import json
import logging
import threading

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from flask import Flask, request, jsonify, abort
from google.oauth2.service_account import Credentials as GCreds
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from gspread.exceptions import WorksheetNotFound

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes, filters
)

from datetime import datetime, timedelta

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(level=logging.INFO)

# ========== ПЕРЕМЕННЫЕ ==========
TOKEN      = os.environ["TELEGRAM_TOKEN"]
SHEET_ID   = os.environ["SHEET_ID"]
CREDS_JSON = os.environ["GSPREAD_CREDENTIALS_JSON"]
FOLDER_ID  = os.environ["DRIVE_FOLDER_ID"]
PORT       = int(os.environ.get("PORT", 8080))

# ========== НАСТРОЙКА GOOGLE SHEETS & DRIVE ==========
# для gspread
ga_scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
ga_creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(CREDS_JSON), ga_scopes)
gc = gspread.authorize(ga_creds)
sheet = gc.open_by_key(SHEET_ID)
# если лист «Заявки» ещё не создан, создаём:
try:
    bookings_ws = sheet.worksheet("Заявки")
except WorksheetNotFound:
    bookings_ws = sheet.add_worksheet(title="Заявки", rows="1000", cols="5")
experts_ws = sheet.worksheet("Эксперты")
users_ws   = sheet.worksheet("Users")

# для googleapiclient (фото)
drive_scopes = ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]
drive_creds = GCreds.from_service_account_info(json.loads(CREDS_JSON), scopes=drive_scopes)
drive_service = build("drive", "v3", credentials=drive_creds)

def upload_file_to_drive(file_storage):
    meta = {"name": file_storage.filename, "parents": [FOLDER_ID]}
    media = MediaIoBaseUpload(file_storage.stream, mimetype=file_storage.mimetype)
    file = drive_service.files().create(body=meta, media_body=media, fields="id").execute()
    drive_service.permissions().create(
        fileId=file["id"],
        body={"type":"anyone","role":"reader"}
    ).execute()
    return f"https://drive.google.com/uc?id={file['id']}"

# ========== FLASK ==========
app = Flask(__name__)

@app.route("/", methods=["GET","HEAD"])
def health():
    return "OK", 200

@app.route("/register-user", methods=["POST"])
def register_user():
    data = request.get_json(silent=True) or {}
    name = data.get("name"); city = data.get("city")
    if not name or not city:
        abort(400, "Missing required field")
    users_ws.append_row([datetime.now().isoformat(), name, city])
    return jsonify({"status":"ok"}), 200

@app.route("/register-expert", methods=["POST"])
def register_expert():
    fio         = request.form.get("fio")
    city        = request.form.get("city")
    sphere      = request.form.get("sphere")
    description = request.form.get("description")
    if not all([fio, city, sphere, description]):
        abort(400, "Missing required field")

    photo_url = ""
    if "photo" in request.files:
        photo_url = upload_file_to_drive(request.files["photo"])

    experts_ws.append_row([
        datetime.now().isoformat(),
        fio, city, sphere, description, photo_url
    ])
    return jsonify({"status":"ok","photo_url":photo_url}), 200

@app.route("/consultation-experts", methods=["GET"])
def get_experts():
    rows = experts_ws.get_all_records()
    return jsonify(rows), 200

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
        datetime.now().isoformat(),
        fio, expert_name, date_str, time_str
    ])
    return jsonify({"status":"ok"}), 200

# ========== HELPERS ДЛЯ БОТА ==========
def get_specialists():
    rows = sheet.worksheet("Эксперты").get_all_records()
    specs = []
    for i, r in enumerate(rows, start=2):
        rec = dict(r)
        slots = []
        s = sheet.worksheet("Эксперты").cell(i, 6).value or ""  # если слоты в колонке F/G
        for part in s.split(";"):
            part = part.strip()
            if part:
                slots.append(part)
        rec["slots"] = slots
        specs.append(rec)
    return specs

def get_specialist_row(telegram_id):
    ws = sheet.worksheet("Эксперты")
    for i, r in enumerate(ws.get_all_records(), start=2):
        if str(r.get("Telegram ID")) == str(telegram_id):
            return ws, i, r
    return None, None, None

# ========== BOT ==========
REG_NAME, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO = range(5)
CHOOSING_REGION, CHOOSING_FIELD, CHOOSING_SPEC, CHOOSING_DATE, CHOOSING_TIME = range(5)

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
    await update.message.reply_text("Пришлите фото сертификата:")
    return REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    file_id = update.message.photo[-1].file_id if update.message.photo else ""
    ws = sheet.worksheet("Эксперты")
    ws.append_row([
        ctx.user_data["fio"],
        ctx.user_data["city"],
        ctx.user_data["field"],
        ctx.user_data["desc"],
        file_id,
        update.effective_user.id,
        update.effective_user.username or "",
        ""  # слоты
    ])
    await update.message.reply_text("Спасибо, вы зарегистрированы как эксперт!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отмена.")
    return ConversationHandler.END

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Добро пожаловать! Выберите команду:\n"
        "/register — регистрация эксперта\n"
        "/time — добавить слоты\n"
        "или начните /start заново."
    )

# тут примера нет, добавьте ваши обработчики выбора region/field/spec/date/time

# ========== СОБИРАЕМ И ЗАПУСКАЕМ ==========
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

application.add_handler(conv_reg)
application.add_handler(CommandHandler("start", cmd_start))
# добавьте остальные хендлеры (time, mainmenu и т. п.)

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    # стартуем Flask в фоне
    threading.Thread(target=run_flask, daemon=True).start()
    # а потом бот
    application.run_polling()
