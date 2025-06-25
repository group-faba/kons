import os
import json
import logging
import gspread
from datetime import datetime, timedelta
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT       = int(os.environ.get('PORT', '8080'))

# --- Google Sheets
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SHEET_ID)
WS_NAME = 'Лист1'

def get_records():
    ws = spreadsheet.worksheet(WS_NAME)
    return ws.get_all_records()

def get_specialist_row(telegram_id):
    ws = spreadsheet.worksheet(WS_NAME)
    rows = ws.get_all_records()
    for idx, row in enumerate(rows, start=2):
        if str(row.get('Telegram ID')) == str(telegram_id):
            return ws, idx, row
    return ws, None, None

def write_slots(telegram_id, date, times):
    ws, row_num, _ = get_specialist_row(telegram_id)
    if row_num:
        col = 8  # H
        cur_val = ws.cell(row_num, col).value
        slots = []
        if cur_val:
            slots = [s.strip() for s in cur_val.split(';') if s.strip()]
        for t in times:
            slot = f"{date} {t}"
            if slot not in slots:
                slots.append(slot)
        ws.update_cell(row_num, col, ';'.join(slots))
        return True
    return False

def get_slots_by_specialist(row):
    slots_str = row.get('')
    if not slots_str:
        return []
    return [s.strip() for s in slots_str.split(';') if s.strip()]

# --- Flask healthcheck
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# --- Conversation для /register
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
    file_id = update.message.photo[-1].file_id if update.message.photo else ''
    ctx.user_data['photo_file_id'] = file_id
    ws = spreadsheet.worksheet(WS_NAME)
    ws.append_row([
        ctx.user_data['fio'],
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

# --- Conversation для /addslot
SLOT_DATE, SLOT_TIMES, SLOT_CONFIRM = range(3)
async def addslot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Выберите дату (сегодня/завтра):",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton((datetime.now()).strftime("%Y-%m-%d"), callback_data="slotdate_" + (datetime.now()).strftime("%Y-%m-%d"))],
            [InlineKeyboardButton((datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d"), callback_data="slotdate_" + (datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d"))]
        ])
    )
    return SLOT_DATE

async def slot_pick_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    date = q.data.split("_")[1]
    ctx.user_data['addslot_date'] = date
    times = [f"{h:02d}:00" for h in range(10, 19)]
    kb = [[InlineKeyboardButton(t, callback_data=f"slottime_{t}")] for t in times]
    kb.append([InlineKeyboardButton("Подтвердить", callback_data="slotconfirm")])
    await q.message.reply_text(f"Дата: {date}\nВыберите время (можно несколько):", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['addslot_times'] = []
    return SLOT_TIMES

async def slot_pick_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    t = q.data.split("_")[1]
    times = ctx.user_data.get('addslot_times', [])
    if t not in times:
        times.append(t)
    ctx.user_data['addslot_times'] = times
    return SLOT_TIMES

async def slot_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    date = ctx.user_data['addslot_date']
    times = ctx.user_data['addslot_times']
    ok = write_slots(q.from_user.id, date, times)
    if ok:
        await q.message.reply_text(f"Время добавлено: {date} — {', '.join(times)}\nВыставить слоты на другой день / изменить? (/addslot)")
    else:
        await q.message.reply_text("Ваша анкета не найдена.")
    return ConversationHandler.END

# --- Основное меню
CHOOSE_REGION, CHOOSE_FIELD, CHOOSE_SPEC, CHOOSE_DATE, CHOOSE_TIME = range(5)
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    records = get_records()
    regions = sorted(list(set([r['Город'] for r in records if r['Город']])))
    kb = [[InlineKeyboardButton(r, callback_data=f"region_{r}")] for r in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSE_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    region = q.data.split("_")[1]
    ctx.user_data['region'] = region
    records = get_records()
    fields = sorted(list(set([r['Сфера'] for r in records if r['Город']==region])))
    kb = [[InlineKeyboardButton(f, callback_data=f"field_{f}")] for f in fields]
    kb.append([InlineKeyboardButton("Назад", callback_data="back_start")])
    await q.message.reply_text(f"Регион: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSE_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    field = q.data.split("_")[1]
    ctx.user_data['field'] = field
    region = ctx.user_data['region']
    records = get_records()
    specs = [r for r in records if r['Город']==region and r['Сфера']==field]
    ctx.user_data['specs'] = specs
    kb = [[InlineKeyboardButton(s['ФИО'], callback_data=f"spec_{i}")] for i, s in enumerate(specs)]
    kb.append([InlineKeyboardButton("Назад", callback_data="back_region")])
    await q.message.reply_text(f"Регион: {region}\nСфера: {field}\nВыберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split("_")[1])
    spec = ctx.user_data['specs'][idx]
    ctx.user_data['selected_specialist'] = spec
    text = f"{spec['ФИО']}\n{spec['Описание']}"
    kb = [
        [InlineKeyboardButton("Назад", callback_data="back_field")],
        [InlineKeyboardButton("Выбрать этого специалиста", callback_data="choose_this")]
    ]
    if spec['photo_file_id']:
        await q.message.reply_photo(photo=spec['photo_file_id'], caption=text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSE_DATE

async def cb_choose_this(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    spec = ctx.user_data['selected_specialist']
    slots = get_slots_by_specialist(spec)
    if not slots:
        await q.message.reply_text("Нет свободного времени для записи.")
        return ConversationHandler.END
    dates = sorted(list(set([s.split()[0] for s in slots])))
    kb = [[InlineKeyboardButton(d, callback_data=f"date_{d}")] for d in dates]
    kb.append([InlineKeyboardButton("Назад", callback_data="back_spec")])
    await q.message.reply_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['slots'] = slots
    return CHOOSE_TIME

async def cb_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    date = q.data.split("_")[1]
    slots = ctx.user_data['slots']
    times = [s.split()[1] for s in slots if s.startswith(date)]
    kb = [[InlineKeyboardButton(t, callback_data=f"time_{t}")] for t in times]
    kb.append([InlineKeyboardButton("Назад", callback_data="back_choose_this")])
    await q.message.reply_text(f"Дата: {date}\nВыберите время:", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['booking_date'] = date
    return CHOOSE_TIME

async def cb_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    t = q.data.split("_")[1]
    date = ctx.user_data['booking_date']
    spec = ctx.user_data['selected_specialist']
    ws, row_num, _ = get_specialist_row(spec['Telegram ID'])
    if not row_num:
        await q.message.reply_text("Ошибка анкеты специалиста.")
        return ConversationHandler.END
    col = 8
    slots_str = ws.cell(row_num, col).value
    slots = [s.strip() for s in slots_str.split(';') if s.strip()]
    slot_full = f"{date} {t}"
    if slot_full not in slots:
        await q.message.reply_text("Это время уже занято.")
        return ConversationHandler.END
    slots.remove(slot_full)
    ws.update_cell(row_num, col, ';'.join(slots))
    await q.message.reply_text(f"Вы записались к специалисту: {spec['ФИО']} на {date} {t}")
    # Уведомление эксперту в личку
    try:
        await ctx.bot.send_message(chat_id=spec['Telegram ID'],
            text=f"У вас новая запись: {q.from_user.full_name} (@{q.from_user.username}) на {date} {t}")
    except Exception:
        pass
    return ConversationHandler.END

# --- Назад
async def cb_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data == "back_start":
        return await cmd_start(q, ctx)
    if data == "back_region":
        return await cb_region(q, ctx)
    if data == "back_field":
        return await cb_field(q, ctx)
    if data == "back_spec":
        return await cb_spec(q, ctx)
    if data == "back_choose_this":
        return await cb_choose_this(q, ctx)
    await q.message.reply_text("Ошибка возврата в меню.")

# --- Handlers
application = ApplicationBuilder().token(TOKEN).build()

application.add_handler(ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CHOOSE_REGION: [CallbackQueryHandler(cb_region, pattern="^region_")],
        CHOOSE_FIELD: [CallbackQueryHandler(cb_field, pattern="^field_"), CallbackQueryHandler(cb_back, pattern="^back_start$")],
        CHOOSE_SPEC: [CallbackQueryHandler(cb_spec, pattern="^spec_"), CallbackQueryHandler(cb_back, pattern="^back_region$")],
        CHOOSE_DATE: [CallbackQueryHandler(cb_choose_this, pattern="^choose_this$"), CallbackQueryHandler(cb_back, pattern="^back_field$")],
        CHOOSE_TIME: [CallbackQueryHandler(cb_date, pattern="^date_"), CallbackQueryHandler(cb_time, pattern="^time_"), CallbackQueryHandler(cb_back, pattern="^back_choose_this$")],
    },
    fallbacks=[]
))
application.add_handler(ConversationHandler(
    entry_points=[CommandHandler("register", reg_start)],
    states={
        REG_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
        REG_CITY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)],
        REG_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field)],
        REG_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_PHOTO: [MessageHandler(filters.PHOTO, reg_photo)],
    },
    fallbacks=[CommandHandler("cancel", reg_cancel)],
))
application.add_handler(ConversationHandler(
    entry_points=[CommandHandler("addslot", addslot_start)],
    states={
        SLOT_DATE: [CallbackQueryHandler(slot_pick_date, pattern="^slotdate_")],
        SLOT_TIMES: [CallbackQueryHandler(slot_pick_time, pattern="^slottime_"), CallbackQueryHandler(slot_confirm, pattern="^slotconfirm$")],
    },
    fallbacks=[]
))

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
