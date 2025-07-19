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

logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT       = int(os.environ.get('PORT', '8080'))

# Google Sheets подключение
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

def add_slots_for_specialist(telegram_id, slots_to_add):
    ws, row_num, _ = get_specialist_row(telegram_id)
    if not row_num:
        return False
    cur_slots = ws.cell(row_num, 9).value or ''
    cur_list = [s.strip() for s in cur_slots.split(';') if s.strip()]
    for slot in slots_to_add:
        if slot not in cur_list:
            cur_list.append(slot)
    ws.update_cell(row_num, 9, ';'.join(sorted(cur_list)))
    return True

def remove_slot(telegram_id, slot):
    ws, row_num, _ = get_specialist_row(telegram_id)
    if not row_num:
        return False
    slots = ws.cell(row_num, 9).value or ''
    slots_list = [s.strip() for s in slots.split(';') if s.strip()]
    if slot in slots_list:
        slots_list.remove(slot)
    ws.update_cell(row_num, 9, ';'.join(slots_list))
    return True

# --- Flask health-check
app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
def health():
    return "OK", 200

# --- ConversationHandler states
REG_NAME, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO = range(5)
SELECT_REGION, SELECT_FIELD, SELECT_SPEC, SELECT_DATE, SELECT_TIME = range(5)
TIME_DATE, TIME_HOUR, TIME_CONFIRM = range(3)

# --- START
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

# --- Добавить время эксперту
async def cb_add_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    ctx.user_data['chosen_slots'] = []
    # Список дат на неделю вперед
    today = datetime.now()
    kb = []
    for i in range(7):
        date = (today + timedelta(days=i)).strftime("%d.%m.%y")
        kb.append([InlineKeyboardButton(date, callback_data=f"time_date_{date}")])
    await update.callback_query.message.reply_text(
        "Выберите дату для добавления времени:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return TIME_DATE

async def time_select_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    date = update.callback_query.data.split('_')[-1]
    ctx.user_data['selected_date'] = date
    hours = [f"{h:02d}:00" for h in range(8, 23)]
    kb = [[InlineKeyboardButton(hour, callback_data=f"time_hour_{hour}")] for hour in hours]
    kb.append([InlineKeyboardButton("Подтвердить", callback_data="time_confirm")])
    await update.callback_query.message.reply_text(
        f"Выбрана дата: {date}\nВыберите часы (можно несколько):",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return TIME_HOUR

async def time_select_hour(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    hour = update.callback_query.data.split('_')[-1]
    date = ctx.user_data['selected_date']
    slot = f"{date} {hour}"
    chosen = ctx.user_data.get('chosen_slots', [])
    if slot in chosen:
        chosen.remove(slot)
    else:
        chosen.append(slot)
    ctx.user_data['chosen_slots'] = chosen
    # Перерисовать кнопки с галочками
    hours = [f"{h:02d}:00" for h in range(8, 23)]
    kb = []
    for h in hours:
        text = f"✅ {h}" if f"{date} {h}" in chosen else h
        kb.append([InlineKeyboardButton(text, callback_data=f"time_hour_{h}")])
    kb.append([InlineKeyboardButton("Подтвердить", callback_data="time_confirm")])
    await update.callback_query.message.edit_text(
        f"Выбрана дата: {date}\nВыберите часы (можно несколько):",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return TIME_HOUR

async def time_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    chosen = ctx.user_data.get('chosen_slots', [])
    if not chosen:
        await update.callback_query.message.reply_text("Вы не выбрали время.")
        return ConversationHandler.END
    add_slots_for_specialist(update.effective_user.id, chosen)
    await update.callback_query.message.reply_text(
        f"Время добавлено: {', '.join(chosen)}"
    )
    return ConversationHandler.END

# --- Блок консультаций
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
    # Кнопки по датам — только даты, которые есть у этого эксперта
    dates = sorted(set(s.split()[0] for s in spec['slots']))
    kb = [[InlineKeyboardButton(d, callback_data=f"specdate_{d}")] for d in dates]
    await update.callback_query.message.reply_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(kb))
    return SELECT_DATE

async def cb_specdate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    date = update.callback_query.data.split('_', 1)[1]
    spec = ctx.user_data['selected_specialist']
    # Все слоты этого специалиста на выбранную дату
    slots = [slot for slot in spec['slots'] if slot.startswith(date)]
    times = [slot.split()[1] for slot in slots]
    kb = [[InlineKeyboardButton(t, callback_data=f"choose_time_{t}")] for t in times]
    kb.append([InlineKeyboardButton("Подтвердить", callback_data="time_confirm_user")])
    ctx.user_data['selected_date'] = date
    ctx.user_data['selected_times'] = []
    await update.callback_query.message.reply_text("Выберите время:", reply_markup=InlineKeyboardMarkup(kb))
    return SELECT_TIME

async def cb_choose_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = update.callback_query.data.split('_', 2)[2]
    selected = ctx.user_data.get('selected_times', [])
    if t in selected:
        selected.remove(t)
    else:
        selected.append(t)
    ctx.user_data['selected_times'] = selected
    # Перерисовать кнопки с галочками
    kb = []
    for tt in selected:
        kb.append([InlineKeyboardButton(f"✅ {tt}", callback_data=f"choose_time_{tt}")])
    await update.callback_query.message.edit_text(
        "Выбранное время (ещё раз нажмите, чтобы убрать):",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return SELECT_TIME

async def cb_time_confirm_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    times = ctx.user_data.get('selected_times', [])
    if not times:
        await update.callback_query.message.reply_text("Вы не выбрали время.")
        return ConversationHandler.END
    date = ctx.user_data['selected_date']
    spec = ctx.user_data['selected_specialist']
    for t in times:
        slot = f"{date} {t}"
        remove_slot(spec['Telegram ID'], slot)
        ws_orders.append_row([
            datetime.now().isoformat(),
            update.effective_user.full_name,
            spec['ФИО эксперта'],
            date, t
        ])
    await update.callback_query.message.reply_text(f"Вы записались к специалисту: {spec['ФИО эксперта']} на {date} {', '.join(times)}")
    return ConversationHandler.END

# --- Conversation Handlers
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
        SELECT_DATE: [CallbackQueryHandler(cb_specdate, pattern=r"^specdate_")],
        SELECT_TIME: [
            CallbackQueryHandler(cb_choose_time, pattern=r"^choose_time_"),
            CallbackQueryHandler(cb_time_confirm_user, pattern=r"time_confirm_user")
        ]
    },
    fallbacks=[]
)

time_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(cb_add_time, pattern="add_time")],
    states={
        TIME_DATE: [CallbackQueryHandler(time_select_date, pattern=r"time_date_")],
        TIME_HOUR: [CallbackQueryHandler(time_select_hour, pattern=r"time_hour_"),
                    CallbackQueryHandler(time_confirm, pattern=r"time_confirm")]
    },
    fallbacks=[]
)

# --- Start bot + Flask health-check
application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(reg_conv)
application.add_handler(consult_conv)
application.add_handler(time_conv)

if __name__ == "__main__":
    import threading
    threading.Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": PORT}, daemon=True).start()
    application.run_polling()
