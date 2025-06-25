import os
import json
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
from collections import defaultdict

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ['TELEGRAM_TOKEN']
SHEET_ID = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT = int(os.environ.get('PORT', '8080'))

SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SHEET_ID)

app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# ----------------- Общие функции ----------------

def get_all_specialists():
    ws = spreadsheet.worksheet('Лист1')
    records = ws.get_all_records()
    return records

def get_specialist_row(telegram_id):
    ws = spreadsheet.worksheet('Лист1')
    records = ws.get_all_records()
    for idx, row in enumerate(records, start=2):  # start=2 из-за заголовка
        if str(row.get("Telegram ID")) == str(telegram_id):
            return ws, idx, row
    return None, None, None

def group_slots_by_date(slots):
    date_map = defaultdict(list)
    for s in slots:
        if " " in s:
            date, time = s.split(" ")
            date_map[date].append(time)
    return date_map

# ----------------- Регистрация специалиста ---------------

REG_FIO, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO = range(5)
async def reg_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите ФИО:")
    return REG_FIO

async def reg_fio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['fio'] = update.message.text
    await update.message.reply_text("Введите город:")
    return REG_CITY

async def reg_city(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['city'] = update.message.text
    await update.message.reply_text("Введите сферу деятельности:")
    return REG_FIELD

async def reg_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['field'] = update.message.text
    await update.message.reply_text("Кратко опишите себя:")
    return REG_DESC

async def reg_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['desc'] = update.message.text
    await update.message.reply_text("Пришлите фото сертификата (или любой документ):")
    return REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    file_id = update.message.photo[-1].file_id if update.message.photo else ''
    ws = spreadsheet.worksheet('Лист1')
    ws.append_row([
        ctx.user_data['fio'],
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        file_id,
        update.effective_user.id,
        update.effective_user.username or ''
    ])
    await update.message.reply_text("Спасибо, вы зарегистрированы как специалист!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отмена регистрации.")
    return ConversationHandler.END

# ----------------- Добавление слотов -------------------

ADDSLOT_DATE, ADDSLOT_TIME = range(2)
async def addslot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Выберите дату для записи:", reply_markup=dates_keyboard())
    return ADDSLOT_DATE

def dates_keyboard():
    from datetime import date, timedelta
    kb = []
    for i in range(7):
        d = (date.today() + timedelta(days=i)).strftime('%Y-%m-%d')
        kb.append([InlineKeyboardButton(d, callback_data=f"slot_date_{d}")])
    return InlineKeyboardMarkup(kb)

async def addslot_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    date_str = q.data.replace("slot_date_", "")
    ctx.user_data['slot_date'] = date_str
    kb = [[InlineKeyboardButton(f"{h}:00", callback_data=f"slot_time_{h:02d}:00")] for h in range(10, 19)]
    kb.append([InlineKeyboardButton("Подтвердить", callback_data="slot_confirm")])
    await q.edit_message_text(
        f"Выбрана дата: {date_str}\nВыберите время (можно несколько):",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    ctx.user_data['slot_times'] = set()
    return ADDSLOT_TIME

async def addslot_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data.startswith("slot_time_"):
        t = q.data.replace("slot_time_", "")
        ctx.user_data['slot_times'] = ctx.user_data.get('slot_times', set())
        if t in ctx.user_data['slot_times']:
            ctx.user_data['slot_times'].remove(t)
        else:
            ctx.user_data['slot_times'].add(t)
        kb = [[InlineKeyboardButton(
            ("✅ " if time in ctx.user_data['slot_times'] else "") + time,
            callback_data=f"slot_time_{time}"
        )] for time in [f"{h:02d}:00" for h in range(10, 19)]]
        kb.append([InlineKeyboardButton("Подтвердить", callback_data="slot_confirm")])
        await q.edit_message_reply_markup(InlineKeyboardMarkup(kb))
        return ADDSLOT_TIME
    elif q.data == "slot_confirm":
        if not ctx.user_data.get('slot_times'):
            await q.answer("Выберите хотя бы одно время")
            return ADDSLOT_TIME
        ws, row_num, _ = get_specialist_row(q.from_user.id)
        if not row_num:
            await q.edit_message_text("Ваша анкета не найдена.")
            return ConversationHandler.END
        slots_str = ws.cell(row_num, 8).value if ws.cell(row_num, 8).value else ""
        slots = slots_str.split(";") if slots_str else []
        new_slots = [f"{ctx.user_data['slot_date']} {t}" for t in sorted(ctx.user_data['slot_times'])]
        slots.extend(new_slots)
        ws.update_cell(row_num, 8, ";".join(slots))
        await q.edit_message_text(
            f"Время добавлено: {ctx.user_data['slot_date']} — {', '.join(sorted(ctx.user_data['slot_times']))}\n\n"
            "Выставить слоты на другой день / изменить? (/addslot)"
        )
        return ConversationHandler.END

# --------------- Клиентский сценарий записи ---------------

CLIENT_REGION, CLIENT_FIELD, CLIENT_SPEC, CLIENT_DATE, CLIENT_TIME = range(5)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    specs = get_all_specialists()
    if not specs:
        await update.message.reply_text("Нет доступных специалистов.")
        return ConversationHandler.END
    regions = sorted(set(s['Город'] for s in specs if s['Город']))
    kb = [[InlineKeyboardButton(r, callback_data=f"region_{r}")] for r in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CLIENT_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    region = q.data.replace("region_", "")
    ctx.user_data['region'] = region
    specs = get_all_specialists()
    fields = sorted(set(s['Сфера'] for s in specs if s['Город'] == region))
    kb = [[InlineKeyboardButton(f, callback_data=f"field_{f}")] for f in fields]
    kb.append([InlineKeyboardButton("Назад", callback_data="back_start")])
    await q.edit_message_text(f"Регион: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb))
    return CLIENT_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    field = q.data.replace("field_", "")
    ctx.user_data['field'] = field
    specs = get_all_specialists()
    candidates = [s for s in specs if s['Город'] == ctx.user_data['region'] and s['Сфера'] == field]
    kb = [[InlineKeyboardButton(c['ФИО'], callback_data=f"spec_{c['Telegram ID']}")] for c in candidates]
    kb.append([InlineKeyboardButton("Назад", callback_data="back_field")])
    await q.edit_message_text(
        f"Регион: {ctx.user_data['region']}\nСфера: {field}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    ctx.user_data['candidates'] = {str(c['Telegram ID']): c for c in candidates}
    return CLIENT_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    spec_id = q.data.replace("spec_", "")
    spec = ctx.user_data['candidates'][spec_id]
    ws, row_num, _ = get_specialist_row(spec_id)
    slots_str = ws.cell(row_num, 8).value if ws.cell(row_num, 8).value else ""
    slots = slots_str.split(";") if slots_str else []
    date_map = group_slots_by_date(slots)
    if not date_map:
        await q.edit_message_text("Нет свободного времени для записи.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(date, callback_data=f"date_{date}")] for date in sorted(date_map.keys())]
    kb.append([InlineKeyboardButton("Назад", callback_data="back_spec")])
    caption = f"{spec['ФИО']}\n{spec['Описание']}"
    if spec['photo_file_id']:
        await q.message.reply_photo(photo=spec['photo_file_id'], caption=caption, reply_markup=InlineKeyboardMarkup(kb))
        await q.delete_message()
    else:
        await q.edit_message_text(caption, reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['slots_map'] = date_map
    ctx.user_data['chosen_spec_id'] = spec_id
    ctx.user_data['chosen_spec'] = spec
    return CLIENT_DATE

async def cb_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    date = q.data.replace("date_", "")
    ctx.user_data['chosen_date'] = date
    slots = ctx.user_data['slots_map'][date]
    kb = [[InlineKeyboardButton(t, callback_data=f"time_{t}")] for t in sorted(slots)]
    kb.append([InlineKeyboardButton("Назад", callback_data="back_date")])
    await q.edit_message_text(
        f"Дата: {date}\nВыберите время для записи:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CLIENT_TIME

async def cb_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    time = q.data.replace("time_", "")
    date = ctx.user_data['chosen_date']
    spec_id = ctx.user_data['chosen_spec_id']
    ws, row_num, _ = get_specialist_row(spec_id)
    slots_str = ws.cell(row_num, 8).value if ws.cell(row_num, 8).value else ""
    slots = slots_str.split(";") if slots_str else []
    slot_to_book = f"{date} {time}"
    if slot_to_book not in slots:
        await q.edit_message_text("К сожалению, это время уже занято.")
        return ConversationHandler.END
    slots.remove(slot_to_book)
    ws.update_cell(row_num, 8, ";".join(slots))
    spec = ctx.user_data['chosen_spec']
    await q.edit_message_text(f"Вы записались к специалисту: {spec['ФИО']} на {date} {time}")
    # Уведомление специалисту
    try:
        await q.bot.send_message(
            chat_id=spec_id,
            text=f"У вас новая запись: {q.from_user.full_name} (@{q.from_user.username}) на {date} {time}"
        )
    except Exception as e:
        logging.warning(f"Не удалось отправить уведомление специалисту: {e}")
    return ConversationHandler.END

# ------------------- Назад и вспомогательные ----------------------

async def back_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)
    return CLIENT_REGION

async def back_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    specs = get_all_specialists()
    regions = sorted(set(s['Город'] for s in specs if s['Город']))
    kb = [[InlineKeyboardButton(r, callback_data=f"region_{r}")] for r in regions]
    await update.callback_query.edit_message_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CLIENT_REGION

async def back_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    specs = get_all_specialists()
    fields = sorted(set(s['Сфера'] for s in specs if s['Город'] == ctx.user_data['region']))
    kb = [[InlineKeyboardButton(f, callback_data=f"field_{f}")] for f in fields]
    kb.append([InlineKeyboardButton("Назад", callback_data="back_start")])
    await update.callback_query.edit_message_text(
        f"Регион: {ctx.user_data['region']}\nВыберите сферу:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CLIENT_FIELD

async def back_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    spec = ctx.user_data['chosen_spec']
    date_map = ctx.user_data['slots_map']
    kb = [[InlineKeyboardButton(date, callback_data=f"date_{date}")] for date in sorted(date_map.keys())]
    kb.append([InlineKeyboardButton("Назад", callback_data="back_spec")])
    caption = f"{spec['ФИО']}\n{spec['Описание']}"
    if spec['photo_file_id']:
        await update.callback_query.message.reply_photo(
            photo=spec['photo_file_id'], caption=caption, reply_markup=InlineKeyboardMarkup(kb)
        )
        await update.callback_query.delete_message()
    else:
        await update.callback_query.edit_message_text(caption, reply_markup=InlineKeyboardMarkup(kb))
    return CLIENT_DATE

# --------- Хендлеры и запуск ---------

application = ApplicationBuilder().token(TOKEN).build()

conv_reg = ConversationHandler(
    entry_points=[CommandHandler("register", reg_start)],
    states={
        REG_FIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_fio)],
        REG_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)],
        REG_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field)],
        REG_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_PHOTO: [MessageHandler(filters.PHOTO, reg_photo)],
    },
    fallbacks=[CommandHandler("cancel", reg_cancel)],
)

conv_addslot = ConversationHandler(
    entry_points=[CommandHandler("addslot", addslot_start)],
    states={
        ADDSLOT_DATE: [CallbackQueryHandler(addslot_date, pattern="^slot_date_")],
        ADDSLOT_TIME: [
            CallbackQueryHandler(addslot_time, pattern="^slot_time_"),
            CallbackQueryHandler(addslot_time, pattern="^slot_confirm$"),
        ],
    },
    fallbacks=[],
)

conv_client = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CLIENT_REGION: [CallbackQueryHandler(cb_region, pattern="^region_")],
        CLIENT_FIELD: [
            CallbackQueryHandler(cb_field, pattern="^field_"),
            CallbackQueryHandler(back_start, pattern="^back_start$")
        ],
        CLIENT_SPEC: [
            CallbackQueryHandler(cb_spec, pattern="^spec_"),
            CallbackQueryHandler(back_field, pattern="^back_field$")
        ],
        CLIENT_DATE: [
            CallbackQueryHandler(cb_date, pattern="^date_"),
            CallbackQueryHandler(back_spec, pattern="^back_spec$")
        ],
        CLIENT_TIME: [
            CallbackQueryHandler(cb_time, pattern="^time_"),
            CallbackQueryHandler(back_date, pattern="^back_date$")
        ]
    },
    fallbacks=[],
)

application.add_handler(conv_reg)
application.add_handler(conv_addslot)
application.add_handler(conv_client)

# ---- Flask для Render ----

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
