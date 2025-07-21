import os
import json
import logging
import gspread
import threading
from google.oauth2.service_account import Credentials
from flask import Flask
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT       = int(os.environ.get('PORT', '8080'))

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_info(CREDS_JSON, scopes=SCOPES)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID)
ws_experts = sheet.worksheet('Эксперты')
ws_users   = sheet.worksheet('Users')
ws_orders  = sheet.worksheet('Заявки')

# --- Работаем ТОЛЬКО через Telegram ID ---
def get_specialist_row_by_id(telegram_id):
    records = ws_experts.get_all_records()
    for i, row in enumerate(records, 2):  # 2 — из-за заголовка
        if str(row.get('Telegram ID')) == str(telegram_id):
            return ws_experts, i, row
    return None, None, None

def remove_slot_for_specialist_by_id(telegram_id, date, time):
    ws, row_num, row = get_specialist_row_by_id(telegram_id)
    if not row_num:
        return False
    cur_slots = ws.cell(row_num, 9).value or ''
    cur_list = [s.strip() for s in cur_slots.split(';') if s.strip()]
    slot = f"{date} {time}"
    if slot in cur_list:
        cur_list.remove(slot)
    ws.update_cell(row_num, 9, ';'.join(cur_list))
    return True

def add_slots_for_specialist_by_id(telegram_id, date, times):
    ws, row_num, _ = get_specialist_row_by_id(telegram_id)
    if not row_num:
        return False
    cur_slots = ws.cell(row_num, 9).value or ''
    cur_list = [s.strip() for s in cur_slots.split(';') if s.strip()]
    for t in times:
        slot = f"{date} {t}"
        if slot not in cur_list:
            cur_list.append(slot)
    ws.update_cell(row_num, 9, ';'.join(sorted(cur_list)))
    return True

# --- остальной код без изменений, просто меняем работу функций на telegram_id ---

REG_NAME, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO = range(5)
SELECT_REGION, SELECT_FIELD, SELECT_SPEC, SELECT_DATE, SELECT_TIME = range(5)
TIME_DATE, TIME_SELECT = range(2)

def get_specialists():
    records = ws_experts.get_all_records()
    specialists = []
    for i, row in enumerate(records, 2):
        spec = dict(row)
        spec['row_num'] = i
        slots = []
        slots_str = row.get('Slots', '')
        if slots_str:
            for el in slots_str.split(';'):
                el = el.strip()
                if el and ' ' in el:
                    slots.append(el)
        spec['slots'] = slots
        specialists.append(spec)
    return specialists

# --- Фолбэк для сброса диалога
async def fallback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("❌ Действие отменено. Главное меню.")
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("❌ Действие отменено. Главное меню.")
    return ConversationHandler.END

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("Нужна консультация", callback_data="need_consult")],
        [InlineKeyboardButton("Зарегистрироваться как эксперт", callback_data="register_expert")],
        [InlineKeyboardButton("Добавить время (слот)", callback_data="add_time")]
    ]
    await update.message.reply_text(
        "Добро пожаловать! Что вы хотите сделать?",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return ConversationHandler.END

# --- Регистрация эксперта
async def cb_register_expert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Введите ваше ФИО:")
    return REG_NAME

async def reg_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['fio'] = update.message.text
    await update.message.reply_text("Введите ваш город:")
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
    await update.message.reply_text("Пришлите фото сертификата или любой документ:")
    return REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    file_id = update.message.photo[-1].file_id if update.message.photo else ''
    ws_experts.append_row([
        ctx.user_data['fio'],
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        file_id,
        update.effective_user.id,   # <-- сюда ID
        update.effective_user.username or '',
        "",
        "" # Slots
    ])
    await update.message.reply_text("✅ Вы зарегистрированы как эксперт!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Регистрация отменена.")
    return ConversationHandler.END

# --- ВЫБОР СЛОТОВ С ГАЛОЧКАМИ ---
def build_time_keyboard(times, selected):
    kb = []
    for t in times:
        label = f"✅ {t}" if t in selected else t
        kb.append([InlineKeyboardButton(label, callback_data=f"time_select_{t}")])
    kb.append([InlineKeyboardButton("Подтвердить", callback_data="time_confirm")])
    return InlineKeyboardMarkup(kb)

async def cb_add_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    today = datetime.now()
    dates = [(today + timedelta(days=i)).strftime("%d.%m.%y") for i in range(7)]
    kb = [[InlineKeyboardButton(date, callback_data=f"time_date_{date}")] for date in dates]
    await update.callback_query.message.reply_text(
        "Выберите дату для добавления слотов:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return TIME_DATE

async def time_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    date = update.callback_query.data.split('_', 2)[2]
    ctx.user_data['selected_date'] = date
    times = [f"{str(h).zfill(2)}:00" for h in range(8, 23)]
    ctx.user_data['selected_times'] = ctx.user_data.get('selected_times', [])
    kb = build_time_keyboard(times, ctx.user_data['selected_times'])
    await update.callback_query.message.reply_text(
        f"Выберите время для {date}:",
        reply_markup=kb
    )
    return TIME_SELECT

async def time_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    time = update.callback_query.data.split('_', 2)[2]
    selected_times = ctx.user_data.get('selected_times', [])
    if time in selected_times:
        selected_times.remove(time)
    else:
        selected_times.append(time)
    ctx.user_data['selected_times'] = selected_times
    times = [f"{str(h).zfill(2)}:00" for h in range(8, 23)]
    kb = build_time_keyboard(times, selected_times)
    await update.callback_query.edit_message_reply_markup(reply_markup=kb)
    return TIME_SELECT

async def time_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    date = ctx.user_data.get('selected_date')
    times = ctx.user_data.get('selected_times', [])
    telegram_id = update.effective_user.id
    if not date or not times:
        await update.callback_query.message.reply_text("Выберите дату и время!")
        return ConversationHandler.END
    add_slots_for_specialist_by_id(telegram_id, date, times)
    await update.callback_query.message.reply_text(
        f"Слоты для {date} добавлены: {', '.join(times)}"
    )
    return ConversationHandler.END

# --- Блок консультаций (запись пользователя) ---
async def cb_need_consult(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    specialists = get_specialists()
    ctx.user_data['specialists'] = specialists
    regions = sorted(set([spec['Город'] for spec in specialists if spec['Город']]))
    kb = [[InlineKeyboardButton(region, callback_data=f"region_{region}")] for region in regions]
    await update.callback_query.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return SELECT_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    region = update.callback_query.data.split('_', 1)[1]
    specialists = ctx.user_data['specialists']
    fields = sorted(set([spec['сфера'] for spec in specialists if spec['Город'] == region and spec['сфера']]))
    ctx.user_data['selected_region'] = region
    kb = [[InlineKeyboardButton(field, callback_data=f"field_{field}")] for field in fields]
    await update.callback_query.message.reply_text(f"Регион: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb))
    return SELECT_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    field = update.callback_query.data.split('_', 1)[1]
    region = ctx.user_data['selected_region']
    specs = [s for s in ctx.user_data['specialists'] if s['Город'] == region and s['сфера'] == field]
    ctx.user_data['selected_field'] = field
    kb = [[InlineKeyboardButton(spec['ФИО эксперта'], callback_data=f"spec_{spec['row_num']}")] for spec in specs]
    await update.callback_query.message.reply_text(f"Сфера: {field}\nВыберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['filtered_specs'] = {str(spec["row_num"]): spec for spec in specs}
    return SELECT_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    spec_row = update.callback_query.data.split('_', 1)[1]
    spec = ctx.user_data['filtered_specs'][spec_row]
    ctx.user_data['selected_specialist'] = spec
    ctx.user_data['selected_expert_id'] = spec['Telegram ID']
    text = f"{spec['ФИО эксперта']}\n{spec['описание']}"
    if spec['photo_file_id']:
        await update.callback_query.message.reply_photo(
            photo=spec['photo_file_id'],
            caption=text
        )
    else:
        await update.callback_query.message.reply_text(text)
    dates = sorted(set([slot.split()[0] for slot in spec['slots']]))
    kb = [[InlineKeyboardButton(date, callback_data=f"date_{date}")] for date in dates]
    await update.callback_query.message.reply_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['slots_of_expert'] = spec['slots']
    return SELECT_DATE

async def cb_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    date = update.callback_query.data.split('_', 1)[1]
    times = [slot.split()[1] for slot in ctx.user_data['slots_of_expert'] if slot.startswith(date)]
    kb = [[InlineKeyboardButton(time, callback_data=f"time_{time}")] for time in times]
    await update.callback_query.message.reply_text(f"Выберите время для {date}:", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['selected_date'] = date
    return SELECT_TIME

async def cb_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    time = update.callback_query.data.split('_', 1)[1]
    date = ctx.user_data['selected_date']
    expert_id = ctx.user_data['selected_expert_id']
    remove_slot_for_specialist_by_id(expert_id, date, time)
    await update.callback_query.message.reply_text(f"Вы записались к специалисту на {date} в {time}. Слот удален из таблицы.")
    return ConversationHandler.END

# --- Flask health-check ---
app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
def health():
    return "OK", 200

# --- Handlers ---
reg_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(cb_register_expert, pattern="register_expert")],
    states={
        REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
        REG_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)],
        REG_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field)],
        REG_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_PHOTO: [MessageHandler(filters.PHOTO, reg_photo)]
    },
    fallbacks=[
        CommandHandler("cancel", fallback),
        CommandHandler("start", fallback),
    ],
)

consult_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(cb_need_consult, pattern="need_consult")],
    states={
        SELECT_REGION: [CallbackQueryHandler(cb_region, pattern=r"^region_")],
        SELECT_FIELD: [CallbackQueryHandler(cb_field, pattern=r"^field_")],
        SELECT_SPEC: [CallbackQueryHandler(cb_spec, pattern=r"^spec_")],
        SELECT_DATE: [CallbackQueryHandler(cb_date, pattern=r"^date_")],
        SELECT_TIME: [CallbackQueryHandler(cb_time, pattern=r"^time_")],
    },
    fallbacks=[
        CommandHandler("cancel", fallback),
        CommandHandler("start", fallback),
    ],
)

time_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(cb_add_time, pattern="add_time")],
    states={
        TIME_DATE: [CallbackQueryHandler(time_date, pattern=r"^time_date_")],
        TIME_SELECT: [
            CallbackQueryHandler(time_select, pattern=r"^time_select_"),
            CallbackQueryHandler(time_confirm, pattern="time_confirm"),
        ]
    },
    fallbacks=[
        CommandHandler("cancel", fallback),
        CommandHandler("start", fallback),
    ],
)

application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(reg_conv)
application.add_handler(consult_conv)
application.add_handler(time_conv)

if __name__ == "__main__":
    threading.Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": PORT}, daemon=True).start()
    application.run_polling()
