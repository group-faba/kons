import os
import json
import logging
import gspread
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
import threading

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

def get_specialist_row(telegram_id):
    records = ws_experts.get_all_records()
    for i, row in enumerate(records, 2):
        if str(row.get('Telegram ID')) == str(telegram_id):
            return ws_experts, i, row
    return None, None, None

def add_slots_for_specialist(telegram_id, date, times):
    ws, row_num, _ = get_specialist_row(telegram_id)
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

# --- Константы для ConversationHandler
REG_NAME, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO = range(5)
SELECT_REGION, SELECT_FIELD, SELECT_SPEC, SELECT_DATE, SELECT_SLOT = range(5)
TIME_DATE, TIME_SELECT, TIME_CONFIRM = range(3)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("Нужна консультация", callback_data="need_consult")],
        [InlineKeyboardButton("Зарегистрироваться как эксперт", callback_data="register_expert")],
        [InlineKeyboardButton("Добавить свободное время", callback_data="add_time")]
    ]
    await update.message.reply_text(
        "Добро пожаловать! Что вы хотите сделать?",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# --- Блок регистрации эксперта
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
        update.effective_user.id,
        update.effective_user.username or '',
        "",
        "" # Slots
    ])
    await update.message.reply_text("✅ Вы зарегистрированы как эксперт!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Регистрация отменена.")
    return ConversationHandler.END

# --- Блок добавления слотов экспертом (через /time)
async def time_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Кнопки с датами на неделю вперед
    today = datetime.now()
    dates = [(today + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
    kb = [[InlineKeyboardButton(date, callback_data=f"time_date_{date}")] for date in dates]
    await update.message.reply_text(
        "Выберите дату для слота:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return TIME_DATE

async def cb_time_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    date = update.callback_query.data.replace('time_date_', '')
    ctx.user_data['time_date'] = date
    times = [f"{str(h).zfill(2)}:00" for h in range(8, 23)]
    kb = [[InlineKeyboardButton(t, callback_data=f"time_select_{t}")] for t in times]
    await update.callback_query.message.reply_text(
        f"Выберите время для {date}:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    ctx.user_data['selected_times'] = []
    return TIME_SELECT

async def cb_time_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    time = update.callback_query.data.replace('time_select_', '')
    if 'selected_times' not in ctx.user_data:
        ctx.user_data['selected_times'] = []
    if time not in ctx.user_data['selected_times']:
        ctx.user_data['selected_times'].append(time)
    kb = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="time_confirm")],
        [InlineKeyboardButton("Добавить ещё время", callback_data="time_more")]
    ]
    await update.callback_query.message.reply_text(
        f"Выбрано: {', '.join(ctx.user_data['selected_times'])}\n\nПодтвердить или добавить ещё?",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return TIME_CONFIRM

async def cb_time_more(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    date = ctx.user_data['time_date']
    times = [f"{str(h).zfill(2)}:00" for h in range(8, 23) if f"{str(h).zfill(2)}:00" not in ctx.user_data['selected_times']]
    kb = [[InlineKeyboardButton(t, callback_data=f"time_select_{t}")] for t in times]
    await update.callback_query.message.reply_text(
        f"Выберите ещё время для {date}:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return TIME_SELECT

async def cb_time_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    date = ctx.user_data['time_date']
    times = ctx.user_data['selected_times']
    telegram_id = update.effective_user.id
    add_slots_for_specialist(telegram_id, date, times)
    await update.callback_query.message.reply_text(
        f"Слоты для {date} в {', '.join(times)} добавлены!"
    )
    return ConversationHandler.END

time_conv = ConversationHandler(
    entry_points=[CommandHandler("time", time_start), CallbackQueryHandler(time_start, pattern="add_time")],
    states={
        TIME_DATE: [CallbackQueryHandler(cb_time_date, pattern=r"^time_date_")],
        TIME_SELECT: [CallbackQueryHandler(cb_time_select, pattern=r"^time_select_")],
        TIME_CONFIRM: [
            CallbackQueryHandler(cb_time_confirm, pattern="time_confirm"),
            CallbackQueryHandler(cb_time_more, pattern="time_more")
        ]
    },
    fallbacks=[]
)

# --- Блок консультаций для пользователя
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
    text = f"{spec['ФИО эксперта']}\n{spec['описание']}"
    if spec['photo_file_id']:
        await update.callback_query.message.reply_photo(
            photo=spec['photo_file_id'],
            caption=text
        )
    else:
        await update.callback_query.message.reply_text(text)
    # --- ВЫВОД КНОПОК СО СВОБОДНЫМИ СЛОТАМИ ---
    slots = spec.get('slots', [])
    if not slots:
        await update.callback_query.message.reply_text(
            "Нет доступных слотов у эксперта.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back")]])
        )
        return SELECT_DATE
    # Группируем по датам и времени
    kb = []
    for slot in slots:
        kb.append([InlineKeyboardButton(slot, callback_data=f"slot_{slot}")])
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="back")])
    await update.callback_query.message.reply_text(
        "Выберите дату и время:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    ctx.user_data['current_slots'] = slots
    return SELECT_DATE

async def cb_slot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    slot = update.callback_query.data.replace('slot_', '')
    spec = ctx.user_data['selected_specialist']
    telegram_id = spec['Telegram ID']
    # Удаляем слот из Google Sheets
    ws, row_num, _ = get_specialist_row(telegram_id)
    if ws and row_num:
        slots = ws.cell(row_num, 9).value or ''
        new_slots = ';'.join([s for s in slots.split(';') if s.strip() != slot])
        ws.update_cell(row_num, 9, new_slots)
    await update.callback_query.message.reply_text(
        f"Вы успешно записались на {slot}!"
    )
    return ConversationHandler.END

reg_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(cb_register_expert, pattern="register_expert")],
    states={
        REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
        REG_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)],
        REG_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field)],
        REG_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_PHOTO: [MessageHandler(filters.PHOTO, reg_photo)]
    },
    fallbacks=[CommandHandler("cancel", reg_cancel)],
)

consult_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(cb_need_consult, pattern="need_consult")],
    states={
        SELECT_REGION: [CallbackQueryHandler(cb_region, pattern=r"^region_")],
        SELECT_FIELD: [CallbackQueryHandler(cb_field, pattern=r"^field_")],
        SELECT_SPEC: [CallbackQueryHandler(cb_spec, pattern=r"^spec_")],
        SELECT_DATE: [CallbackQueryHandler(cb_slot, pattern=r"^slot_")]
    },
    fallbacks=[]
)

# --- Flask health-check
app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
def health():
    return "OK", 200

application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(reg_conv)
application.add_handler(consult_conv)
application.add_handler(time_conv)

if __name__ == "__main__":
    threading.Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": PORT}, daemon=True).start()
    application.run_polling()
