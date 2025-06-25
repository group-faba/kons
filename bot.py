import os
import json
import gspread
import logging
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

# --- Логирование
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
ws = spreadsheet.worksheet('Лист1')

# --- Flask healthcheck (для Render)
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# --- Служебные функции
def get_specialists():
    """Возвращает список специалистов (каждый — dict)."""
    rows = ws.get_all_records()
    return rows

def get_spec_by_id(user_id):
    rows = ws.get_all_records()
    for i, r in enumerate(rows):
        if str(r.get('Telegram ID', '')) == str(user_id):
            return i+2, r  # +2 потому что первая строка — заголовки, индексация с 1
    return None, None

def get_slots(spec_row):
    """Получить доступные слоты (возвращает list[('2025-06-25 10:00'), ...])."""
    slot_str = ws.cell(spec_row, 8).value if ws.cell(spec_row, 8).value else ""
    slots = [x.strip() for x in slot_str.split(';') if x.strip()]
    return slots

def update_slots(spec_row, slots):
    slot_str = ';'.join(slots)
    ws.update_cell(spec_row, 8, slot_str)

# --- /start: выбор региона -> сферы -> специалиста
START_REGION, START_FIELD, START_SPEC, START_CONFIRM = range(4)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    rows = get_specialists()
    regions = sorted(list(set(r['Город'] for r in rows)))
    kb = [[InlineKeyboardButton(region, callback_data=f"region|{region}")] for region in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return START_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data.split('|', 1)[1]
    ctx.user_data['region'] = region
    rows = get_specialists()
    fields = sorted(list(set(r['Сфера'] for r in rows if r['Город'] == region)))
    kb = [[InlineKeyboardButton(field, callback_data=f"field|{field}")] for field in fields]
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb)
    )
    return START_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = update.callback_query.data.split('|', 1)[1]
    ctx.user_data['field'] = field
    rows = get_specialists()
    specs = [r for r in rows if r['Город'] == ctx.user_data['region'] and r['Сфера'] == field]
    ctx.user_data['spec_list'] = specs
    kb = [
        [InlineKeyboardButton(spec['ФИО'], callback_data=f"spec|{spec['Telegram ID']}")]
        for spec in specs
    ]
    await update.callback_query.edit_message_text(
        f"Регион: {ctx.user_data['region']}\nСфера: {field}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return START_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    spec_id = update.callback_query.data.split('|', 1)[1]
    rows = ctx.user_data['spec_list']
    spec = next((r for r in rows if str(r['Telegram ID']) == spec_id), None)
    if not spec:
        await update.callback_query.edit_message_text("Специалист не найден.")
        return ConversationHandler.END

    spec_row, _ = get_spec_by_id(spec_id)
    slots = get_slots(spec_row)
    if not slots:
        await update.callback_query.edit_message_text(
            f"{spec['ФИО']}\n{spec['Описание']}\nНет свободного времени для записи."
        )
        return ConversationHandler.END

    ctx.user_data['chosen_spec'] = spec
    ctx.user_data['spec_row'] = spec_row

    text = f"{spec['ФИО']}\n{spec['Описание']}"
    kb = [[InlineKeyboardButton(slot, callback_data=f"slot|{slot}")] for slot in slots]
    kb.append([InlineKeyboardButton("Назад", callback_data="back")])
    if spec.get('photo_file_id'):
        await update.callback_query.message.reply_photo(
            photo=spec['photo_file_id'],
            caption=text,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        try:
            await update.callback_query.delete_message()
        except: pass
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return START_CONFIRM

async def cb_slot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    slot = update.callback_query.data.split('|', 1)[1]
    spec = ctx.user_data['chosen_spec']
    spec_row = ctx.user_data['spec_row']
    slots = get_slots(spec_row)
    if slot not in slots:
        await update.callback_query.edit_message_text("Время уже занято.")
        return ConversationHandler.END
    # Удаляем слот
    slots.remove(slot)
    update_slots(spec_row, slots)
    await update.callback_query.edit_message_text(
        f"Вы записались к специалисту: {spec['ФИО']} на {slot}"
    )
    # Уведомление эксперту
    if spec['Telegram ID']:
        try:
            await update.callback_query.bot.send_message(
                int(spec['Telegram ID']),
                f"У вас новая запись!\nКлиент: @{update.effective_user.username or update.effective_user.id}\nДата и время: {slot}"
            )
        except: pass
    return ConversationHandler.END

async def cb_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    # Вернуться на выбор специалистов
    kb = [
        [InlineKeyboardButton(spec['ФИО'], callback_data=f"spec|{spec['Telegram ID']}")]
        for spec in ctx.user_data['spec_list']
    ]
    await update.callback_query.message.reply_text(
        f"Регион: {ctx.user_data['region']}\nСфера: {ctx.user_data['field']}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    try:
        await update.callback_query.delete_message()
    except: pass
    return START_SPEC

# --- /addslot — добавить себе слоты (для эксперта)
ADDSLOT_DATE, ADDSLOT_TIMES, ADDSLOT_CONFIRM = range(10, 13)

def get_date_buttons():
    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)
    kb = [
        [InlineKeyboardButton(today.strftime('%d.%m.%Y'), callback_data=f"slotdate|{today}")],
        [InlineKeyboardButton(tomorrow.strftime('%d.%m.%Y'), callback_data=f"slotdate|{tomorrow}")],
        [InlineKeyboardButton("Другая дата", callback_data="slotdate_other")]
    ]
    return kb

async def addslot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = get_date_buttons()
    await update.message.reply_text("Выберите дату для записи:", reply_markup=InlineKeyboardMarkup(kb))
    return ADDSLOT_DATE

async def addslot_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    if data == "slotdate_other":
        await update.callback_query.edit_message_text("Введите дату в формате ДД.ММ.ГГГГ:")
        ctx.user_data['manual_date'] = True
        return ADDSLOT_DATE
    date_str = data.split('|', 1)[1]
    ctx.user_data['slot_date'] = date_str
    # Кнопки по времени (10:00-18:00)
    times = [f"{h:02d}:00" for h in range(10, 19)]
    kb = [[InlineKeyboardButton(t, callback_data=f"slottime|{t}")] for t in times]
    kb.append([InlineKeyboardButton("Подтвердить", callback_data="slotok")])
    ctx.user_data['chosen_times'] = set()
    await update.callback_query.edit_message_text(
        f"Дата: {datetime.strptime(date_str, '%Y-%m-%d').strftime('%d.%m.%Y')}\nВыберите время (можно несколько):",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return ADDSLOT_TIMES

async def addslot_date_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        date = datetime.strptime(update.message.text.strip(), "%d.%m.%Y").date()
    except Exception:
        await update.message.reply_text("Неверный формат. Введите дату ДД.ММ.ГГГГ:")
        return ADDSLOT_DATE
    ctx.user_data['slot_date'] = str(date)
    times = [f"{h:02d}:00" for h in range(10, 19)]
    kb = [[InlineKeyboardButton(t, callback_data=f"slottime|{t}")] for t in times]
    kb.append([InlineKeyboardButton("Подтвердить", callback_data="slotok")])
    ctx.user_data['chosen_times'] = set()
    await update.message.reply_text(
        f"Дата: {date.strftime('%d.%m.%Y')}\nВыберите время (можно несколько):",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return ADDSLOT_TIMES

async def addslot_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    if data == "slotok":
        if not ctx.user_data.get('chosen_times'):
            await update.callback_query.answer("Выберите хотя бы одно время.")
            return ADDSLOT_TIMES
        # Добавляем слоты в таблицу
        user_id = update.effective_user.id
        spec_row, spec = get_spec_by_id(user_id)
        if not spec:
            await update.callback_query.edit_message_text("Ваша анкета не найдена.")
            return ConversationHandler.END
        slots = get_slots(spec_row)
        date_str = ctx.user_data['slot_date']
        new_slots = [f"{date_str} {t}" for t in ctx.user_data['chosen_times']]
        slots += [s for s in new_slots if s not in slots]
        update_slots(spec_row, slots)
        msg = f"Время добавлено на {datetime.strptime(date_str, '%Y-%m-%d').strftime('%d.%m.%Y')}: {', '.join(ctx.user_data['chosen_times'])}"
        kb = [
            [InlineKeyboardButton("Добавить на другой день", callback_data="addnextday")],
            [InlineKeyboardButton("Изменить для этой даты", callback_data="addsame")]
        ]
        await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb))
        return ADDSLOT_CONFIRM
    # Время (можно множественный выбор, отмечаем выбранные)
    t = data.split('|', 1)[1]
    times = ctx.user_data.get('chosen_times', set())
    if t in times:
        times.remove(t)
    else:
        times.add(t)
    ctx.user_data['chosen_times'] = times
    # Обновляем кнопки с галочками
    kb = [
        [InlineKeyboardButton(f"{'✅' if hh in times else ''} {hh}", callback_data=f"slottime|{hh}")]
        for hh in [f"{h:02d}:00" for h in range(10, 19)]
    ]
    kb.append([InlineKeyboardButton("Подтвердить", callback_data="slotok")])
    await update.callback_query.edit_message_text(
        f"Дата: {datetime.strptime(ctx.user_data['slot_date'], '%Y-%m-%d').strftime('%d.%m.%Y')}\nВыберите время (можно несколько):",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return ADDSLOT_TIMES

async def addslot_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    if data == "addnextday":
        kb = get_date_buttons()
        await update.callback_query.edit_message_text(
            "Выберите дату для записи:", reply_markup=InlineKeyboardMarkup(kb)
        )
        return ADDSLOT_DATE
    elif data == "addsame":
        # Повторный выбор времени для той же даты
        times = [f"{h:02d}:00" for h in range(10, 19)]
        kb = [
            [InlineKeyboardButton(t, callback_data=f"slottime|{t}")] for t in times
        ]
        kb.append([InlineKeyboardButton("Подтвердить", callback_data="slotok")])
        ctx.user_data['chosen_times'] = set()
        await update.callback_query.edit_message_text(
            f"Дата: {datetime.strptime(ctx.user_data['slot_date'], '%Y-%m-%d').strftime('%d.%m.%Y')}\nВыберите время (можно несколько):",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return ADDSLOT_TIMES
    return ConversationHandler.END

# --- Регистрация эксперта
REG_NAME, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO = range(20, 25)
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
    await update.message.reply_text("Опишите себя в двух словах:")
    return REG_DESC

async def reg_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['desc'] = update.message.text
    await update.message.reply_text("Пришлите фото сертификата (или любой документ):")
    return REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    file_id = update.message.photo[-1].file_id if update.message.photo else ''
    fio = ctx.user_data['fio']
    city = ctx.user_data['city']
    field = ctx.user_data['field']
    desc = ctx.user_data['desc']
    user_id = update.effective_user.id
    username = update.effective_user.username or ''
    ws.append_row([fio, city, field, desc, file_id, user_id, username, ""])
    await update.message.reply_text("Спасибо, вы зарегистрированы как специалист!")
    return ConversationHandler.END

# --- Application и Handlers
application = ApplicationBuilder().token(TOKEN).build()

conv_start = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        START_REGION: [CallbackQueryHandler(cb_region, pattern="^region\\|")],
        START_FIELD: [CallbackQueryHandler(cb_field, pattern="^field\\|")],
        START_SPEC: [
            CallbackQueryHandler(cb_spec, pattern="^spec\\|"),
        ],
        START_CONFIRM: [
            CallbackQueryHandler(cb_slot, pattern="^slot\\|"),
            CallbackQueryHandler(cb_back, pattern="^back$"),
        ],
    },
    fallbacks=[],
)

conv_addslot = ConversationHandler(
    entry_points=[CommandHandler("addslot", addslot_start)],
    states={
        ADDSLOT_DATE: [
            CallbackQueryHandler(addslot_date, pattern="^slotdate"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, addslot_date_text),
        ],
        ADDSLOT_TIMES: [
            CallbackQueryHandler(addslot_time, pattern="^slottime|^slotok"),
        ],
        ADDSLOT_CONFIRM: [
            CallbackQueryHandler(addslot_confirm, pattern="^addnextday|^addsame"),
        ],
    },
    fallbacks=[],
)

conv_reg = ConversationHandler(
    entry_points=[CommandHandler("register", reg_start)],
    states={
        REG_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
        REG_CITY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)],
        REG_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field)],
        REG_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_PHOTO: [MessageHandler(filters.PHOTO, reg_photo)],
    },
    fallbacks=[],
)

application.add_handler(conv_start)
application.add_handler(conv_addslot)
application.add_handler(conv_reg)

# --- Render, запуск
def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
