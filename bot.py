import os
import json
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
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

# --- Google Sheets подключение
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SHEET_ID)

def get_specialists():
    ws = spreadsheet.worksheet('Лист1')
    records = ws.get_all_records()
    specialists = []
    for i, row in enumerate(records, 2):  # начиная со 2-й строки
        spec = dict(row)
        spec['row_num'] = i
        # Получаем список слотов для этого специалиста
        slots = []
        slots_str = ws.cell(i, 8).value  # 8 - колонка H (Slots)
        if slots_str:
            for el in slots_str.split(';'):
                el = el.strip()
                if el and ' ' in el:
                    slots.append(el)
        spec['slots'] = slots
        specialists.append(spec)
    return specialists

def get_specialist_row(telegram_id):
    ws = spreadsheet.worksheet('Лист1')
    records = ws.get_all_records()
    for i, row in enumerate(records, 2):
        if str(row.get('Telegram ID')) == str(telegram_id):
            return ws, i, row
    return None, None, None

def add_slots_for_specialist(telegram_id, date, times):
    ws, row_num, _ = get_specialist_row(telegram_id)
    if not row_num:
        return False
    cur_slots = ws.cell(row_num, 8).value or ''
    cur_list = [s.strip() for s in cur_slots.split(';') if s.strip()]
    for t in times:
        slot = f"{date} {t}"
        if slot not in cur_list:
            cur_list.append(slot)
    ws.update_cell(row_num, 8, ';'.join(sorted(cur_list)))
    return True

# --- Flask healthcheck (чтобы Render не засыпал)
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# --- Conversation для /register (анкета)
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
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        ctx.user_data['photo_file_id'] = file_id
    else:
        ctx.user_data['photo_file_id'] = ''
    ws = spreadsheet.worksheet('Лист1')
    fio = ctx.user_data['fio']
    ws.append_row([
        fio,
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        ctx.user_data['photo_file_id'],
        update.effective_user.id,
        update.effective_user.username or '',
        ""  # пустой столбец Slots
    ])
    await update.message.reply_text("Спасибо, вы зарегистрированы как специалист!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отмена регистрации.")
    return ConversationHandler.END

async def send_webapp_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton(
            "Открыть мини-приложение",
            web_app=WebAppInfo(url="https://group-faba.github.io/telegram_kons/")
        )]
    ]
    await update.message.reply_text(
        "Запусти мини-приложение для записи на консультацию:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# --- Новый Хендлер /time ---
TIME_DATE, TIME_SELECT = range(2)

async def time_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    dates = [(datetime.now() + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
    kb = [[InlineKeyboardButton(d, callback_data=f'timedate_{d}')] for d in dates]
    await update.message.reply_text("Выберите дату для записи:", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['slot_times'] = []
    return TIME_DATE

async def time_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    date = q.data.split('_')[1]
    ctx.user_data['slot_date'] = date
    times = [f"{h:02d}:00" for h in range(8, 21)]  # 8:00–20:00 МСК
    # Показываем галочки для выбранных
    kb = []
    selected = set(ctx.user_data.get('slot_times', []))
    for t in times:
        text = f"{'✅ ' if t in selected else ''}{t}"
        kb.append([InlineKeyboardButton(text, callback_data=f'timeselect_{t}')])
    kb.append([InlineKeyboardButton("Подтвердить", callback_data='timeconfirm')])
    kb.append([InlineKeyboardButton("Назад", callback_data='timeback')])
    await q.message.edit_text(
        f"Дата: {date}\nВыберите время (можно несколько):",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return TIME_SELECT

async def time_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data.startswith('timeselect_'):
        time = q.data.split('_')[1]
        slot_times = ctx.user_data.get('slot_times', [])
        if time in slot_times:
            slot_times.remove(time)
        else:
            slot_times.append(time)
        ctx.user_data['slot_times'] = slot_times
        # Перерисовать кнопки с галочками
        times = [f"{h:02d}:00" for h in range(8, 21)]
        kb = []
        selected = set(slot_times)
        for t in times:
            text = f"{'✅ ' if t in selected else ''}{t}"
            kb.append([InlineKeyboardButton(text, callback_data=f'timeselect_{t}')])
        kb.append([InlineKeyboardButton("Подтвердить", callback_data='timeconfirm')])
        kb.append([InlineKeyboardButton("Назад", callback_data='timeback')])
        await q.message.edit_text(
            f"Дата: {ctx.user_data['slot_date']}\nВыберите время (можно несколько):",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return TIME_SELECT
    elif q.data == 'timeconfirm':
        date = ctx.user_data['slot_date']
        times = ctx.user_data.get('slot_times', [])
        if not times:
            await q.message.reply_text("Не выбрано время.")
            return TIME_SELECT
        success = add_slots_for_specialist(q.from_user.id, date, times)
        if not success:
            await q.message.reply_text("Ваша анкета не найдена.")
        else:
            await q.message.reply_text(f"Время добавлено: {date} — {', '.join(times)}")
            await q.message.reply_text("Выставить слоты на другой день / изменить? (/time)")
        return ConversationHandler.END
    elif q.data == 'timeback':
        return await time_start(update, ctx)
    return TIME_SELECT

# --- Логика выбора специалиста
CHOOSING_REGION, CHOOSING_FIELD, CHOOSING_SPEC, CHOOSING_DATE, CHOOSING_TIME = range(5)
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Очищаем клавиатуру предыдущих сообщений (если есть)
    try:
        if update.message:
            await update.message.reply_text("Главное меню обновлено.", reply_markup=InlineKeyboardMarkup([]))
        elif update.callback_query:
            await update.callback_query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup([]))
    except Exception:
        pass
    specialists = get_specialists()
    regions = sorted(set([spec['Город'] for spec in specialists]))
    kb = [[InlineKeyboardButton(r, callback_data=f'region_{r}')] for r in regions]
    await update.effective_message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['specialists'] = specialists
    return CHOOSING_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    region = q.data.split('_', 1)[1]
    specialists = ctx.user_data['specialists']
    fields = sorted(set([spec['Сфера'] for spec in specialists if spec['Город'] == region]))
    ctx.user_data['selected_region'] = region
    kb = [[InlineKeyboardButton(f, callback_data=f'field_{f}')] for f in fields]
    kb.append([InlineKeyboardButton("Назад", callback_data='mainmenu')])
    await q.message.reply_text(f"Регион: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    field = q.data.split('_', 1)[1]
    region = ctx.user_data['selected_region']
    specs = [s for s in ctx.user_data['specialists'] if s['Город'] == region and s['Сфера'] == field]
    ctx.user_data['selected_field'] = field
    kb = [[InlineKeyboardButton(spec['ФИО'], callback_data=f'spec_{spec["Telegram ID"]}')] for spec in specs]
    kb.append([InlineKeyboardButton("Назад", callback_data='regionback')])
    await q.message.reply_text(f"Регион: {region}\nСфера: {field}\nВыберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['filtered_specs'] = {str(spec["Telegram ID"]): spec for spec in specs}
    return CHOOSING_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    spec_id = q.data.split('_', 1)[1]
    spec = ctx.user_data['filtered_specs'][spec_id]
    ctx.user_data['selected_specialist'] = spec
    # Готовим уникальные даты из слотов
    dates = sorted(set(s.split()[0] for s in spec['slots']))
    kb = [[InlineKeyboardButton(d, callback_data=f'date_{d}')] for d in dates]
    kb.append([InlineKeyboardButton("Назад", callback_data='fieldback')])
    text = f"{spec['ФИО']}\n{spec['Описание']}"
    if spec.get('photo_file_id'):
        await q.message.reply_photo(
            photo=spec['photo_file_id'],
            caption=text
        )
        await q.message.reply_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await q.message.reply_text(text)
        await q.message.reply_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_DATE

async def cb_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    selected_date = q.data.split('_', 1)[-1]
    ctx.user_data['selected_date'] = selected_date

    spec = ctx.user_data['selected_specialist']
    slots = [slot for slot in spec['slots'] if slot.startswith(selected_date)]
    times = [slot.split()[1] for slot in slots]

    if not times:
        await q.message.reply_text('Нет свободного времени для этой даты.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="specback")]]))
        return CHOOSING_TIME

    kb = [[InlineKeyboardButton(t, callback_data=f"time_{t}")] for t in times]
    kb.append([InlineKeyboardButton("Назад", callback_data="specback")])
    await q.message.reply_text("Выберите время для записи:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_TIME

async def cb_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    time = q.data.split('_', 1)[-1]
    date = ctx.user_data['selected_date']
    spec = ctx.user_data['selected_specialist']
    fio = spec['ФИО']
    ws, row_num, _ = get_specialist_row(spec['Telegram ID'])
    if not row_num:
        await q.message.reply_text("Ошибка: не найдена анкета специалиста.")
        return ConversationHandler.END
    slots_str = ws.cell(row_num, 8).value or ''
    slots = [s.strip() for s in slots_str.split(';') if s.strip()]
    slot = f"{date} {time}"
    if slot not in slots:
        await q.message.reply_text("Это время уже занято.")
        return ConversationHandler.END
    slots.remove(slot)
    ws.update_cell(row_num, 8, ';'.join(slots))
    await q.message.reply_text(f"Вы записались к специалисту: {fio} на {slot}")
    try:
        await ctx.bot.send_message(
            spec['Telegram ID'],
            f"У вас новая запись: {update.effective_user.full_name} (@{update.effective_user.username}) на {slot}"
        )
    except Exception:
        pass
    return ConversationHandler.END

async def cb_mainmenu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await cmd_start(update, ctx)

async def cb_regionback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    specialists = ctx.user_data['specialists']
    regions = sorted(set([spec['Город'] for spec in specialists]))
    kb = [[InlineKeyboardButton(r, callback_data=f'region_{r}')] for r in regions]
    await update.callback_query.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_REGION

async def cb_fieldback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    region = ctx.user_data['selected_region']
    specialists = ctx.user_data['specialists']
    fields = sorted(set([spec['Сфера'] for spec in specialists if spec['Город'] == region]))
    kb = [[InlineKeyboardButton(f, callback_data=f'field_{f}')] for f in fields]
    kb.append([InlineKeyboardButton("Назад", callback_data='mainmenu')])
    await update.callback_query.message.reply_text(f"Регион: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_FIELD

async def cb_specback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    region = ctx.user_data['selected_region']
    field = ctx.user_data['selected_field']
    specs = [s for s in ctx.user_data['specialists'] if s['Город'] == region and s['Сфера'] == field]
    kb = [[InlineKeyboardButton(spec['ФИО'], callback_data=f'spec_{spec["Telegram ID"]}')] for spec in specs]
    kb.append([InlineKeyboardButton("Назад", callback_data='regionback')])
    await update.callback_query.message.reply_text(f"Регион: {region}\nСфера: {field}\nВыберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['filtered_specs'] = {str(spec["Telegram ID"]): spec for spec in specs}
    return CHOOSING_SPEC

# --- Application и Handlers
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

conv_time = ConversationHandler(
    entry_points=[CommandHandler("time", time_start)],
    states={
        TIME_DATE: [CallbackQueryHandler(time_date, pattern=r'^timedate_')],
        TIME_SELECT: [CallbackQueryHandler(time_select, pattern=r'^timeselect_|^timeconfirm|^timeback')],
    },
    fallbacks=[],
)

conv_main = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CHOOSING_REGION: [CallbackQueryHandler(cb_region, pattern=r'^region_')],
        CHOOSING_FIELD: [
            CallbackQueryHandler(cb_field, pattern=r'^field_'),
            CallbackQueryHandler(cb_mainmenu, pattern='^mainmenu$')
        ],
        CHOOSING_SPEC: [
            CallbackQueryHandler(cb_spec, pattern=r'^spec_'),
            CallbackQueryHandler(cb_regionback, pattern='^regionback')
        ],
        CHOOSING_DATE: [
            CallbackQueryHandler(cb_date, pattern=r'^date_'),
            CallbackQueryHandler(cb_fieldback, pattern='^fieldback')
        ],
        CHOOSING_TIME: [
            CallbackQueryHandler(cb_time, pattern=r'^time_'),
            CallbackQueryHandler(cb_specback, pattern='^specback')
        ]
    },
    fallbacks=[],
)

application.add_handler(conv_reg)
application.add_handler(conv_time)
application.add_handler(conv_main)
application.add_handler(CommandHandler("webapp", send_webapp_button))

async def handle_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    import json
    data = update.message.web_app_data.data
    form = json.loads(data)
    fio = form.get("fio", "")
    city = form.get("city", "")
    await update.message.reply_text(f"Спасибо! Получено: {fio}, {city}")

application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))

# --- Flask + polling для Render
def run_flask():
    app.run(host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
