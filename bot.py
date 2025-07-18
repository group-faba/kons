# bot.py
import os
import json
import logging
from datetime import datetime, timedelta

import gspread
from google.oauth2.service_account import Credentials
from flask import Flask, request

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

# ——— Настройка логирования ———
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["TELEGRAM_TOKEN"]
PORT = int(os.environ.get("PORT", "8443"))

# ——— Google Sheets ———
# одно и то же креденшл для бота и сервера
CREDS_JSON = os.environ["GSPREAD_CREDENTIALS_JSON"]
creds_dict = json.loads(CREDS_JSON)
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc = gspread.authorize(creds)
SHEET_ID = os.environ["SHEET_ID"]
sheet = gc.open_by_key(SHEET_ID)
experts_ws = sheet.worksheet("Эксперты")
bookings_ws = None
try:
    bookings_ws = sheet.worksheet("Заявки")
except gspread.exceptions.WorksheetNotFound:
    bookings_ws = sheet.add_worksheet("Заявки", rows="1000", cols="5")

# ——— Flask-часть для healthcheck и webhook ———
app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
def health():
    return "OK", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    application.process_update(update)
    return "", 200

# ——— ConversationState ———
(CHOOSING_ACTION,
 CHOOSING_REGION,
 CHOOSING_FIELD,
 CHOOSING_SPEC,
 CHOOSING_DATE,
 CHOOSING_TIME,
 REG_NAME,
 REG_CITY,
 REG_SPHERE,
 REG_DESC,
 REG_PHOTO) = range(11)

# ——— MAIN MENU (/start) ———
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # унификация: reply в message или callback_query
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        target = q.message
        await target.edit_reply_markup(reply_markup=None)
    else:
        target = update.message

    keyboard = [
        [InlineKeyboardButton("📋 Консультации", callback_data="CONSULT")],
        [InlineKeyboardButton("✒️ Регистрация эксперта", callback_data="REGISTER")],
    ]
    await target.reply_text(
        "Выберите действие:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING_ACTION

# ——— GET SPECIALISTS ———
def get_specialists():
    rows = experts_ws.get_all_records()
    specs = []
    for i, r in enumerate(rows, start=2):
        r["row"] = i
        # подготовка слотов
        raw = r.get("Slots", "")
        r["slots"] = [s.strip() for s in raw.split(";") if s.strip()]
        specs.append(r)
    return specs

# ——— CONSULT FLOW ———
async def start_consult(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # кнопка «📋 Консультации»
    q = update.callback_query
    await q.answer()
    await q.message.edit_reply_markup(reply_markup=None)

    specs = get_specialists()
    ctx.user_data["specialists"] = specs
    regions = sorted({r["город эксперта"] for r in specs})
    kb = [[InlineKeyboardButton(r, callback_data=f"REGION|{r}")] for r in regions]
    await q.message.reply_text("Выберите город эксперта:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    _, region = q.data.split("|", 1)
    ctx.user_data["region"] = region

    specs = [s for s in ctx.user_data["specialists"] if s["город эксперта"] == region]
    fields = sorted({s["сфера"] for s in specs})
    ctx.user_data["filtered"] = specs

    kb = [[InlineKeyboardButton(f, callback_data=f"FIELD|{f}")] for f in fields]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="CONSULT")])
    await q.message.edit_text(f"Город: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    _, field = q.data.split("|", 1)
    specs = [s for s in ctx.user_data["filtered"] if s["сфера"] == field]
    ctx.user_data["filtered"] = specs

    kb = [
        [InlineKeyboardButton(s["ФИО эксперта"], callback_data=f"SPEC|{s['row']}")]
        for s in specs
    ]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="REGION|" + ctx.user_data["region"])])
    await q.message.edit_text(f"Сфера: {field}\nВыберите эксперта:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    _, row = q.data.split("|", 1)
    spec = next(s for s in ctx.user_data["filtered"] if str(s["row"]) == row)
    ctx.user_data["selected_spec"] = spec

    # показываем имя, описание, фото (если есть photo_url)
    text = f"{spec['ФИО эксперта']}\n\n{spec.get('описание','')}"
    if spec.get("photo_url"):
        await q.message.reply_photo(photo=spec["photo_url"], caption=text)
    else:
        await q.message.reply_text(text)

    # даты из слотов
    dates = sorted({slot.split()[0] for slot in spec["slots"]})
    kb = [[InlineKeyboardButton(d, callback_data=f"DATE|{d}")] for d in dates]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="FIELD|" + spec["сфера"])])
    await q.message.reply_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_DATE

async def cb_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    _, date = q.data.split("|", 1)
    ctx.user_data["chosen_date"] = date

    spec = ctx.user_data["selected_spec"]
    times = [slot.split()[1] for slot in spec["slots"] if slot.startswith(date)]
    kb = [[InlineKeyboardButton(t, callback_data=f"TIME|{t}")] for t in times]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="SPEC|" + str(spec["row"]))])
    await q.message.edit_text("Выберите время:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_TIME

async def cb_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    _, time = q.data.split("|", 1)

    me = ctx.user_data
    fio = q.from_user.full_name
    spec = me["selected_spec"]
    date = me["chosen_date"]

    # записываем в таблицу
    bookings_ws.append_row([
        datetime.now().isoformat(),
        fio,
        spec["ФИО эксперта"],
        spec["город эксперта"],
        spec["сфера"],
        date,
        time,
    ])

    await q.message.reply_text(f"✅ Вы записаны к {spec['ФИО эксперта']} на {date} {time}")
    return ConversationHandler.END

# ——— REGISTER FLOW ———
async def start_register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # унификация message/callback_query
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        target = q.message
        await target.edit_reply_markup(reply_markup=None)
    else:
        target = update.message

    await target.reply_text("Введите ваше ФИО:")
    return REG_NAME

async def reg_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["fio"] = update.message.text
    await update.message.reply_text("Введите ваш город:")
    return REG_CITY

async def reg_city(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["city"] = update.message.text
    await update.message.reply_text("Введите вашу сферу:")
    return REG_SPHERE

async def reg_sphere(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["sphere"] = update.message.text
    await update.message.reply_text("Кратко опишите себя:")
    return REG_DESC

async def reg_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["desc"] = update.message.text
    await update.message.reply_text("Пришлите ваше фото (или нажмите /skip):")
    return REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pid = ""
    if update.message.photo:
        pid = update.message.photo[-1].file_id
    # добавляем в Google Sheets
    experts_ws.append_row([
        datetime.now().isoformat(),
        ctx.user_data["fio"],
        ctx.user_data["city"],
        ctx.user_data["sphere"],
        ctx.user_data["desc"],
        pid
    ])
    await update.message.reply_text("✅ Вы зарегистрированы как эксперт!")
    return ConversationHandler.END

async def skip_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # если фото не прислали
    experts_ws.append_row([
        datetime.now().isoformat(),
        ctx.user_data["fio"],
        ctx.user_data["city"],
        ctx.user_data["sphere"],
        ctx.user_data["desc"],
        ""
    ])
    await update.message.reply_text("✅ Регистрация завершена (без фото).")
    return ConversationHandler.END

async def cancel_reg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Регистрация отменена.")
    return ConversationHandler.END

# ——— Конфигурация ConversationHandlers ———
consult_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_consult, pattern="^CONSULT$")],
    states={
        CHOOSING_REGION: [CallbackQueryHandler(cb_region, pattern="^REGION\\|")],
        CHOOSING_FIELD:  [CallbackQueryHandler(cb_field,  pattern="^FIELD\\|")],
        CHOOSING_SPEC:   [CallbackQueryHandler(cb_spec,   pattern="^SPEC\\|")],
        CHOOSING_DATE:   [CallbackQueryHandler(cb_date,   pattern="^DATE\\|")],
        CHOOSING_TIME:   [CallbackQueryHandler(cb_time,   pattern="^TIME\\|")],
    },
    fallbacks=[CommandHandler("start", cmd_start)],
    per_message=False,
)

reg_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_register, pattern="^REGISTER$"),
                  CommandHandler("start", start_register),
                  CommandHandler("register", start_register)],
    states={
        REG_NAME:   [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
        REG_CITY:   [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)],
        REG_SPHERE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_sphere)],
        REG_DESC:   [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_PHOTO:  [
            MessageHandler(filters.PHOTO, reg_photo),
            CommandHandler("skip", skip_photo),
        ],
    },
    fallbacks=[CommandHandler("cancel", cancel_reg)],
    per_message=False,
)

# ——— Сборка и запуск ———
application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(consult_conv)
application.add_handler(reg_conv)

if __name__ == "__main__":
    # запускаем и Flask (для webhooks) и polling
    import threading
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT), daemon=True).start()
    application.run_polling()
