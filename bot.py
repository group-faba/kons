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
from datetime import datetime, timedelta

# === Логирование ===
logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT       = int(os.environ.get('PORT', '8080'))

# === Google Sheets ===
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SHEET_ID)
ws = spreadsheet.worksheet('Лист1')

def get_records():
    return ws.get_all_records()

def save_slot(telegram_id, date, times):
    # Найти строку по Telegram ID и добавить время в колонку H
    all_rows = ws.get_all_values()
    for idx, row in enumerate(all_rows):
        if str(telegram_id) == str(row[5]):
            now = ws.cell(idx+1, 8).value or ""
            # Склеиваем новые слоты в строку, формат: ДАТА ЧАС;ЧАС;ЧАС (на одну дату)
            timestr = f"{date} " + ";".join(times)
            if now:
                ws.update_cell(idx+1, 8, now + ";" + timestr)
            else:
                ws.update_cell(idx+1, 8, timestr)
            break

def get_specialists():
    records = get_records()
    return records

def get_unique_regions():
    records = get_records()
    return sorted(set([rec['Город'] for rec in records if rec['Город']]))

def get_fields_by_region(region):
    records = get_records()
    return sorted(set([rec['Сфера'] for rec in records if rec['Город'] == region and rec['Сфера']]))

def get_specs_by_region_field(region, field):
    records = get_records()
    return [rec for rec in records if rec['Город'] == region and rec['Сфера'] == field]

def get_spec_by_fio(fio):
    records = get_records()
    for rec in records:
        if rec['ФИО'] == fio:
            return rec
    return None

def parse_slots(cell):
    # Слоты в формате: "26.06.2025 10:00;11:00;12:00;27.06.2025 14:00;15:00"
    slots = {}
    if not cell:
        return slots
    parts = cell.split(";")
    current_date = None
    for p in parts:
        if " " in p:
            current_date, time = p.split()
            slots.setdefault(current_date, []).append(time)
        elif current_date:
            slots.setdefault(current_date, []).append(p)
    return slots

# === Flask health-check ===
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# === Conversation ===
(
    REG_NAME, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO,
    CHOOSE_REGION, CHOOSE_FIELD, CHOOSE_SPEC, SHOW_SPEC, CONFIRM_SPEC,
    SLOT_DATE, SLOT_TIME, SLOT_CONFIRM
) = range(13)

# --- Регистрация специалиста ---
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
    await update.message.reply_text("Пришлите фото сертификата/документа (можно пропустить, напишите - нет):")
    return REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    else:
        file_id = ''
    ctx.user_data['photo_file_id'] = file_id
    fio = ctx.user_data['fio']
    ws.append_row([
        fio,
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        ctx.user_data['photo_file_id'],
        update.effective_user.id,
        update.effective_user.username or '',
        "",  # слоты
    ])
    await update.message.reply_text("Спасибо, вы зарегистрированы как специалист!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отмена регистрации.")
    return ConversationHandler.END

# --- Добавление слотов (расписания) ---
async def addslot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # выбор даты: сегодня или завтра
    today = datetime.now().date()
    days = [today, today + timedelta(days=1)]
    kb = [
        [InlineKeyboardButton(day.strftime('%d.%m.%Y'), callback_data=day.strftime('%d.%m.%Y'))]
        for day in days
    ]
    await update.message.reply_text("Выберите дату для расписания:", reply_markup=InlineKeyboardMarkup(kb))
    return SLOT_DATE

async def addslot_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    date = update.callback_query.data
    ctx.user_data['slot_date'] = date
    # Выбор часов — много кнопок
    hours = [f"{h:02d}:00" for h in range(10, 19)]
    kb = [
        [InlineKeyboardButton(hour, callback_data=hour)]
        for hour in hours
    ]
    kb.append([InlineKeyboardButton("Подтвердить", callback_data="confirm")])
    ctx.user_data['selected_times'] = []
    await update.callback_query.edit_message_text(
        f"Дата: {date}\nВыберите время (можно несколько):",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return SLOT_TIME

async def addslot_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    time = query.data
    if time == "confirm":
        # сохранить в таблицу
        slot_date = ctx.user_data['slot_date']
        selected_times = ctx.user_data.get('selected_times', [])
        save_slot(update.effective_user.id, slot_date, selected_times)
        await query.edit_message_text(f"Слоты добавлены: {slot_date} {', '.join(selected_times)}")
        return ConversationHandler.END
    else:
        # добавляем/убираем время (переключатель)
        selected = ctx.user_data.get('selected_times', [])
        if time in selected:
            selected.remove(time)
        else:
            selected.append(time)
        ctx.user_data['selected_times'] = selected
        # обновляем кнопки с галочками
        hours = [f"{h:02d}:00" for h in range(10, 19)]
        kb = []
        for hour in hours:
            mark = "✅ " if hour in selected else ""
            kb.append([InlineKeyboardButton(mark + hour, callback_data=hour)])
        kb.append([InlineKeyboardButton("Подтвердить", callback_data="confirm")])
        await query.edit_message_text(
            f"Дата: {ctx.user_data['slot_date']}\nВыберите время (можно несколько):",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return SLOT_TIME

# --- Главная логика: выбор специалиста и запись ---
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # 1. Выбор региона
    regions = get_unique_regions()
    kb = [
        [InlineKeyboardButton(region, callback_data=region)]
        for region in regions
    ]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSE_REGION

async def cb_choose_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data
    ctx.user_data['region'] = region
    fields = get_fields_by_region(region)
    kb = [
        [InlineKeyboardButton(field, callback_data=field)]
        for field in fields
    ]
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nВыберите сферу:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSE_FIELD

async def cb_choose_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = update.callback_query.data
    ctx.user_data['field'] = field
    specs = get_specs_by_region_field(ctx.user_data['region'], field)
    kb = [
        [InlineKeyboardButton(spec['ФИО'], callback_data=spec['ФИО'])]
        for spec in specs
    ]
    await update.callback_query.edit_message_text(
        f"Сфера: {field}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSE_SPEC

async def cb_choose_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    fio = update.callback_query.data
    ctx.user_data['fio'] = fio
    spec = get_spec_by_fio(fio)
    # Отправляем карточку
    kb = [
        [InlineKeyboardButton("Назад", callback_data="back_field")],
        [InlineKeyboardButton("Выбрать этого специалиста", callback_data="confirm_spec")]
    ]
    text = f"{spec['ФИО']}\n{spec['Описание']}"
    if spec.get('photo_file_id'):
        await update.callback_query.message.reply_photo(
            photo=spec['photo_file_id'],
            caption=text,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        await update.callback_query.delete_message()
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return SHOW_SPEC

async def cb_back_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    # Возврат к выбору сферы
    region = ctx.user_data['region']
    fields = get_fields_by_region(region)
    kb = [
        [InlineKeyboardButton(field, callback_data=field)]
        for field in fields
    ]
    await update.callback_query.message.reply_text(
        f"Регион: {region}\nВыберите сферу:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    await update.callback_query.delete_message()
    return CHOOSE_FIELD

async def cb_confirm_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    fio = ctx.user_data['fio']
    spec = get_spec_by_fio(fio)
    # показываем доступные слоты
    slots_cell = spec.get('') or spec.get('Слоты', '') or spec.get('H', '')
    slots = parse_slots(slots_cell)
    today = datetime.now().date().strftime('%d.%m.%Y')
    slot_list = []
    if today in slots:
        slot_list = slots[today]
    if slot_list:
        kb = [
            [InlineKeyboardButton(time, callback_data="slot_"+time)]
            for time in slot_list
        ]
        await update.callback_query.message.reply_text(
            "Выберите время для записи:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
    else:
        await update.callback_query.message.reply_text(
            "Нет свободных слотов у специалиста."
        )
    await update.callback_query.delete_message()
    return CONFIRM_SPEC

async def cb_pick_slot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    time = update.callback_query.data.replace("slot_", "")
    fio = ctx.user_data['fio']
    await update.callback_query.edit_message_text(
        f"Вы записались к специалисту: {fio} на {time}"
    )
    # (Здесь можно добавить запись в таблицу клиента)
    return ConversationHandler.END

# === Хендлеры и запуск ===
application = ApplicationBuilder().token(TOKEN).build()

conv_reg = ConversationHandler(
    entry_points=[CommandHandler("register", reg_start)],
    states={
        REG_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
        REG_CITY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)],
        REG_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field)],
        REG_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_PHOTO: [MessageHandler(filters.PHOTO | filters.TEXT, reg_photo)],
    },
    fallbacks=[CommandHandler("cancel", reg_cancel)],
)

conv_slot = ConversationHandler(
    entry_points=[CommandHandler("addslot", addslot_start)],
    states={
        SLOT_DATE: [CallbackQueryHandler(addslot_date)],
        SLOT_TIME: [CallbackQueryHandler(addslot_time)],
    },
    fallbacks=[],
)

conv_main = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CHOOSE_REGION: [CallbackQueryHandler(cb_choose_region)],
        CHOOSE_FIELD: [CallbackQueryHandler(cb_choose_field)],
        CHOOSE_SPEC: [
            CallbackQueryHandler(cb_choose_spec)
        ],
        SHOW_SPEC: [
            CallbackQueryHandler(cb_back_field, pattern="^back_field$"),
            CallbackQueryHandler(cb_confirm_spec, pattern="^confirm_spec$"),
        ],
        CONFIRM_SPEC: [
            CallbackQueryHandler(cb_pick_slot, pattern="^slot_"),
        ],
    },
    fallbacks=[],
)

application.add_handler(conv_reg)
application.add_handler(conv_slot)
application.add_handler(conv_main)

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
