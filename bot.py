import os
import json
import logging
import gspread
from datetime import datetime, timedelta
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
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

SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SHEET_ID)
worksheet = spreadsheet.worksheet('Лист1')

# --- Flask healthcheck (для Render)
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# --- Константы для ConversationHandler
START_REGION, START_FIELD, START_SPEC, SELECT_DATE, SELECT_TIME = range(5)
REG_NAME, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO = range(5)

# --- Помощники Google Sheet
def get_all_records():
    return worksheet.get_all_records()

def get_regions():
    records = get_all_records()
    return sorted(set(r['Город'] for r in records if r['Город']))

def get_fields(region):
    records = get_all_records()
    return sorted(set(r['Сфера'] for r in records if r['Город'] == region and r['Сфера']))

def get_specs(region, field):
    records = get_all_records()
    return [r for r in records if r['Город'] == region and r['Сфера'] == field]

def get_spec_by_id(tg_id):
    records = get_all_records()
    for idx, r in enumerate(records, 2): # c 2й строки
        if str(r['Telegram ID']) == str(tg_id):
            return r, idx
    return None, None

def get_slots(spec_row):
    slots = []
    for val in worksheet.row_values(spec_row)[8:]:  # после Username
        if val.strip():
            slots.extend(val.strip().split(';'))
    slots = [s for s in slots if s]
    return slots

def update_slots(spec_row, slots):
    # пишем в H колонку (9-я) весь список как "дата время;дата время"
    slots_str = ';'.join(slots)
    worksheet.update_cell(spec_row, 8, slots_str)  # H-колонка

# --- REGISTRATION (анкета)
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
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    else:
        file_id = ''
    ctx.user_data['photo_file_id'] = file_id
    fio = ctx.user_data['fio']
    city = ctx.user_data['city']
    field = ctx.user_data['field']
    desc = ctx.user_data['desc']
    telegram_id = update.effective_user.id
    username = update.effective_user.username or ''
    worksheet.append_row([
        fio, city, field, desc, file_id, telegram_id, username, ""
    ])
    await update.message.reply_text("Спасибо, вы зарегистрированы как специалист!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отмена регистрации.")
    return ConversationHandler.END

# --- /addslot: добавить слоты
async def addslot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['addslot'] = {}
    # выбираем даты на 7 дней вперёд
    today = datetime.now()
    kb = []
    for i in range(7):
        d = today + timedelta(days=i)
        s = d.strftime("%Y-%m-%d")
        kb.append([InlineKeyboardButton(s, callback_data=f"asdate|{s}")])
    await update.message.reply_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(kb))
    return 200

async def addslot_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    date = update.callback_query.data.split('|')[1]
    ctx.user_data['addslot']['date'] = date
    # показываем часы 10:00 ... 18:00
    kb = []
    for h in range(10, 19):
        kb.append([InlineKeyboardButton(f"{h:02d}:00", callback_data=f"astime|{h:02d}:00")])
    kb.append([InlineKeyboardButton("Подтвердить", callback_data="asconfirm")])
    kb.append([InlineKeyboardButton("Назад", callback_data="asback")])
    ctx.user_data['addslot']['times'] = set()
    await update.callback_query.edit_message_text(
        f"Дата: {date}\nВыберите время (можно несколько):",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return 201

async def addslot_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    t = update.callback_query.data.split('|')[1]
    ctx.user_data['addslot']['times'].add(t)
    # отмечаем выбранные (галочкой)
    times = ctx.user_data['addslot']['times']
    date = ctx.user_data['addslot']['date']
    kb = []
    for h in range(10, 19):
        txt = f"{h:02d}:00"
        if txt in times:
            txt = "✅ " + txt
        kb.append([InlineKeyboardButton(txt, callback_data=f"astime|{h:02d}:00")])
    kb.append([InlineKeyboardButton("Подтвердить", callback_data="asconfirm")])
    kb.append([InlineKeyboardButton("Назад", callback_data="asback")])
    await update.callback_query.edit_message_text(
        f"Дата: {date}\nВыбранные: {', '.join(times) if times else 'нет'}\nМожно выбрать несколько.",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return 201

async def addslot_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    times = ctx.user_data['addslot']['times']
    date = ctx.user_data['addslot']['date']
    if not times:
        await update.callback_query.edit_message_text("Вы не выбрали время.")
        return ConversationHandler.END
    # ищем свою анкету по Telegram ID
    user_id = update.effective_user.id
    spec, row = get_spec_by_id(user_id)
    if not spec:
        await update.callback_query.edit_message_text("Ваша анкета не найдена.")
        return ConversationHandler.END
    # Добавляем к имеющимся слотам новые
    old_slots = get_slots(row)
    for t in times:
        slot = f"{date} {t}"
        if slot not in old_slots:
            old_slots.append(slot)
    update_slots(row, old_slots)
    await update.callback_query.edit_message_text(
        f"Время добавлено: {date} — {', '.join(sorted(times))}\n\n"
        "Выставить слоты на другой день / изменить? (/addslot)"
    )
    return ConversationHandler.END

async def addslot_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    # назад к дате
    today = datetime.now()
    kb = []
    for i in range(7):
        d = today + timedelta(days=i)
        s = d.strftime("%Y-%m-%d")
        kb.append([InlineKeyboardButton(s, callback_data=f"asdate|{s}")])
    await update.callback_query.edit_message_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(kb))
    return 200

# --- /start: выбор специалиста → даты → время
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    regions = get_regions()
    kb = [[InlineKeyboardButton(r, callback_data=f"region|{r}")] for r in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return START_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data.split('|',1)[1]
    ctx.user_data['region'] = region
    fields = get_fields(region)
    kb = [[InlineKeyboardButton(f, callback_data=f"field|{f}")] for f in fields]
    kb.append([InlineKeyboardButton("Назад", callback_data="backstart")])
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb)
    )
    return START_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = update.callback_query.data.split('|',1)[1]
    region = ctx.user_data['region']
    ctx.user_data['field'] = field
    specs = get_specs(region, field)
    ctx.user_data['spec_list'] = specs
    kb = [
        [InlineKeyboardButton(f"{spec['ФИО']}", callback_data=f"spec|{spec['Telegram ID']}")]
        for spec in specs
    ]
    kb.append([InlineKeyboardButton("Назад", callback_data="backregion")])
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nСфера: {field}\nВыберите специалиста:",
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
    all_slots = get_slots(spec_row)
    if not all_slots:
        await update.callback_query.edit_message_text(
            f"{spec['ФИО']}\n{spec['Описание']}\nНет свободного времени для записи."
        )
        return ConversationHandler.END

    # Собираем список уникальных дат
    dates = sorted(set(slot.split()[0] for slot in all_slots))
    ctx.user_data['chosen_spec'] = spec
    ctx.user_data['spec_row'] = spec_row
    ctx.user_data['all_slots'] = all_slots

    text = f"{spec['ФИО']}\n{spec['Описание']}\n\nВыберите дату:"
    kb = [[InlineKeyboardButton(
        datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m.%Y"), callback_data=f"date|{d}")]
        for d in dates]
    kb.append([InlineKeyboardButton("Назад", callback_data="backspec")])
    # Отправляем фото и даты
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
    return SELECT_DATE

async def cb_select_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    date = update.callback_query.data.split('|', 1)[1]
    ctx.user_data['chosen_date'] = date
    all_slots = ctx.user_data['all_slots']
    times = sorted(slot.split()[1] for slot in all_slots if slot.startswith(date))
    kb = [[InlineKeyboardButton(time, callback_data=f"time|{time}")] for time in times]
    kb.append([InlineKeyboardButton("Назад", callback_data="backdate")])
    await update.callback_query.edit_message_text(
        f"Дата: {datetime.strptime(date, '%Y-%m-%d').strftime('%d.%m.%Y')}\nВыберите время:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return SELECT_TIME

async def cb_select_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    time = update.callback_query.data.split('|', 1)[1]
    date = ctx.user_data['chosen_date']
    slot = f"{date} {time}"
    spec = ctx.user_data['chosen_spec']
    spec_row = ctx.user_data['spec_row']
    all_slots = get_slots(spec_row)
    if slot not in all_slots:
        await update.callback_query.edit_message_text("Время уже занято.")
        return ConversationHandler.END
    # Удаляем слот
    all_slots.remove(slot)
    update_slots(spec_row, all_slots)
    await update.callback_query.edit_message_text(
        f"Вы записались к специалисту: {spec['ФИО']} на {datetime.strptime(date, '%Y-%m-%d').strftime('%d.%m.%Y')} в {time}"
    )
    # Уведомление эксперту
    if spec['Telegram ID']:
        try:
            await update.callback_query.bot.send_message(
                int(spec['Telegram ID']),
                f"У вас новая запись!\nКлиент: @{update.effective_user.username or update.effective_user.id}\nДата: {datetime.strptime(date, '%Y-%m-%d').strftime('%d.%m.%Y')} Время: {time}"
            )
        except: pass
    return ConversationHandler.END

# --- Back/Назад
async def cb_backregion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)
    return START_REGION

async def cb_backspec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    region = ctx.user_data['region']
    await cb_region(update, ctx)
    return START_FIELD

async def cb_backdate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cb_spec(update, ctx)
    return SELECT_DATE

async def cb_backstart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)
    return START_REGION

# --- Application и Handlers
application = ApplicationBuilder().token(TOKEN).build()

# REG
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

# ADDSLOT
conv_addslot = ConversationHandler(
    entry_points=[CommandHandler("addslot", addslot_start)],
    states={
        200: [CallbackQueryHandler(addslot_date, pattern="^asdate\\|")],
        201: [
            CallbackQueryHandler(addslot_time, pattern="^astime\\|"),
            CallbackQueryHandler(addslot_confirm, pattern="^asconfirm$"),
            CallbackQueryHandler(addslot_back, pattern="^asback$")
        ]
    },
    fallbacks=[]
)

# MAIN
conv_main = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        START_REGION: [CallbackQueryHandler(cb_region, pattern="^region\\|")],
        START_FIELD: [
            CallbackQueryHandler(cb_field, pattern="^field\\|"),
            CallbackQueryHandler(cb_backstart, pattern="^backstart$")
        ],
        START_SPEC: [
            CallbackQueryHandler(cb_spec, pattern="^spec\\|"),
            CallbackQueryHandler(cb_backregion, pattern="^backregion$")
        ],
        SELECT_DATE: [
            CallbackQueryHandler(cb_select_date, pattern="^date\\|"),
            CallbackQueryHandler(cb_backspec, pattern="^backspec$")
        ],
        SELECT_TIME: [
            CallbackQueryHandler(cb_select_time, pattern="^time\\|"),
            CallbackQueryHandler(cb_backdate, pattern="^backdate$")
        ]
    },
    fallbacks=[]
)

application.add_handler(conv_reg)
application.add_handler(conv_addslot)
application.add_handler(conv_main)

# --- Стартуем Flask и Telegram Application
def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
