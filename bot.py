import os
import json
import logging
from datetime import datetime, timedelta
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

# --- Flask healthcheck (чтобы Render не засыпал)
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# --- STATES
(
    START_REGION, START_FIELD, START_SPEC,
    TIME_CHOOSE_DATE, TIME_CHOOSE_MONTH, TIME_CHOOSE_YEAR, TIME_CHOOSE_HOUR, TIME_CONFIRM,
    REG_NAME, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO
) = range(12)

# --- Получить уникальные значения из таблицы
def get_unique(column):
    ws = spreadsheet.worksheet('Лист1')
    values = ws.col_values(column)[1:]
    return sorted(list(set(values)))

def get_specialists(region=None, field=None):
    ws = spreadsheet.worksheet('Лист1')
    rows = ws.get_all_records()
    result = []
    for row in rows:
        if region and row['Город'] != region:
            continue
        if field and row['Сфера'] != field:
            continue
        result.append(row)
    return result

def get_specialist_row(telegram_id):
    ws = spreadsheet.worksheet('Лист1')
    rows = ws.get_all_records()
    for idx, row in enumerate(rows, start=2):
        if str(row.get("Telegram ID", "")) == str(telegram_id):
            return ws, idx, row
    return None, None, None

# --- /start (выбор региона)
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    regions = get_unique(2)
    kb = [[InlineKeyboardButton(f"🌎 {r}", callback_data=f"region|{r}")] for r in regions]
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return START_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    region = q.data.split('|')[1]
    ctx.user_data['region'] = region
    fields = get_unique(3)
    kb = [[InlineKeyboardButton(f"💼 {f}", callback_data=f"field|{f}")] for f in fields]
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
    await q.edit_message_text(f"Регион: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb))
    return START_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    field = q.data.split('|')[1]
    ctx.user_data['field'] = field
    specs = get_specialists(ctx.user_data.get('region'), field)
    kb = [[InlineKeyboardButton(f"👤 {s['ФИО']}", callback_data=f"spec|{s['Telegram ID']}")] for s in specs]
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
    await q.edit_message_text(
        f"Регион: {ctx.user_data['region']}\nСфера: {field}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    ctx.user_data['specs'] = {str(s['Telegram ID']): s for s in specs}
    return START_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    spec_id = q.data.split('|')[1]
    spec = ctx.user_data['specs'][spec_id]
    caption = f"{spec['ФИО']}\n{spec['Описание']}"
    kb = [
        [InlineKeyboardButton("🔙 Назад", callback_data="back")],
        [InlineKeyboardButton("✅ Выбрать этого специалиста", callback_data=f"choose|{spec_id}")]
    ]
    if spec['photo_file_id']:
        await q.message.reply_photo(photo=spec['photo_file_id'], caption=caption, reply_markup=InlineKeyboardMarkup(kb))
        await q.delete_message()
    else:
        await q.edit_message_text(caption, reply_markup=InlineKeyboardMarkup(kb))
    # Слоты (даты)
    ws, row_num, _ = get_specialist_row(spec_id)
    if row_num:
        slots_str = ws.cell(row_num, 8).value  # H column
        if slots_str:
            all_slots = [x.strip() for x in slots_str.split(';') if x.strip()]
            # Сортируем и группируем по датам
            date_set = sorted(set(s.split()[0] for s in all_slots))
            kb_dates = [[InlineKeyboardButton(f"📅 {d}", callback_data=f"date|{spec_id}|{d}")] for d in date_set]
            kb_dates.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
            await q.message.reply_text("Доступные даты:", reply_markup=InlineKeyboardMarkup(kb_dates))
        else:
            await q.message.reply_text("Нет свободного времени для записи.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back")]]))
    else:
        await q.message.reply_text("Нет свободного времени для записи.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back")]]))
    return ConversationHandler.END

async def cb_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, spec_id, date_str = q.data.split('|')
    ws, row_num, spec = get_specialist_row(spec_id)
    slots_str = ws.cell(row_num, 8).value
    slots = [x.strip() for x in slots_str.split(';') if x.strip() and x.startswith(date_str)]
    # Показываем только время
    kb = [[InlineKeyboardButton(f"🕒 {s.split()[1]}", callback_data=f"book|{spec_id}|{s}")] for s in slots]
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
    await q.message.reply_text(f"Дата: {date_str}\nВыберите время:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_book(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, spec_id, slot_str = q.data.split('|')
    ws, row_num, spec = get_specialist_row(spec_id)
    slots_str = ws.cell(row_num, 8).value
    slots = [x.strip() for x in slots_str.split(';') if x.strip()]
    if slot_str not in slots:
        await q.message.reply_text("Это время уже занято.")
        return ConversationHandler.END
    slots.remove(slot_str)
    ws.update_cell(row_num, 8, ';'.join(slots))
    await q.message.reply_text(f"Вы записались к специалисту: {spec['ФИО']} на {slot_str} (МСК)")
    # Уведомление эксперту
    try:
        await update.get_bot().send_message(
            spec_id, f"У вас новая запись: {update.effective_user.full_name} (@{update.effective_user.username}) на {slot_str}"
        )
    except Exception:
        pass
    return ConversationHandler.END

# --- /time (добавление слотов)
async def cmd_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Показать дни на неделю вперёд
    days = [(datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    kb = [[InlineKeyboardButton(f"📅 {d}", callback_data=f"time_date|{d}")] for d in days]
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
    await update.message.reply_text("Выберите дату для добавления слотов:", reply_markup=InlineKeyboardMarkup(kb))
    return TIME_CHOOSE_DATE

async def cb_time_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    date_str = q.data.split('|')[1]
    ctx.user_data['add_date'] = date_str
    # Список часов 08:00 - 21:00
    hours = [f"{str(h).zfill(2)}:00" for h in range(8, 22)]
    kb = [[InlineKeyboardButton(f"🕒 {h}", callback_data=f"time_hour|{h}")] for h in hours]
    kb.append([InlineKeyboardButton("✅ Подтвердить", callback_data="time_confirm")])
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
    ctx.user_data['add_slots'] = []
    await q.message.reply_text(
        f"Дата: {date_str}\nВыберите ваши свободные часы для консультации (МСК):", 
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return TIME_CHOOSE_HOUR

async def cb_time_hour(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    hour = q.data.split('|')[1]
    slots = ctx.user_data.get('add_slots', [])
    if hour not in slots:
        slots.append(hour)
    ctx.user_data['add_slots'] = slots
    await q.answer(f"Добавлено: {', '.join(slots)}")

async def cb_time_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    date = ctx.user_data.get('add_date')
    slots = ctx.user_data.get('add_slots', [])
    if not date or not slots:
        await q.message.reply_text("Вы не выбрали дату или время.")
        return ConversationHandler.END
    ws, row_num, spec = get_specialist_row(update.effective_user.id)
    if not row_num:
        await q.message.reply_text("Ваша анкета не найдена.")
        return ConversationHandler.END
    slots_str = ws.cell(row_num, 8).value
    all_slots = [x.strip() for x in slots_str.split(';') if x.strip()] if slots_str else []
    for h in slots:
        all_slots.append(f"{date} {h}")
    ws.update_cell(row_num, 8, ';'.join(all_slots))
    await q.message.reply_text(
        f"Время добавлено: {date} — {', '.join(slots)}\nДобавить время на другой день? (нажмите кнопку ниже)",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Добавить время на другой день", callback_data="time_repeat")],
            [InlineKeyboardButton("✅ Завершить", callback_data="time_done")]
        ])
    )
    return ConversationHandler.END

async def cb_time_repeat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    # просто заново вызовем cmd_time
    await cmd_time(q, ctx)
    return TIME_CHOOSE_DATE

# --- Регистрация (минимум)
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
    fio = ctx.user_data['fio']
    ws = spreadsheet.worksheet('Лист1')
    ws.append_row([
        fio,
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        ctx.user_data['photo_file_id'],
        update.effective_user.id,
        update.effective_user.username or '',
        ''
    ])
    await update.message.reply_text("Спасибо, вы зарегистрированы как специалист!")
    return ConversationHandler.END

# --- Handlers
application = ApplicationBuilder().token(TOKEN).build()

conv_start = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        START_REGION: [CallbackQueryHandler(cb_region, pattern=r'^region\|')],
        START_FIELD:  [CallbackQueryHandler(cb_field, pattern=r'^field\|')],
        START_SPEC:   [CallbackQueryHandler(cb_spec, pattern=r'^spec\|')],
    },
    fallbacks=[CallbackQueryHandler(cmd_start, pattern='^back$')],
    per_message=True,
    allow_reentry=True,
)

conv_time = ConversationHandler(
    entry_points=[CommandHandler("time", cmd_time)],
    states={
        TIME_CHOOSE_DATE: [CallbackQueryHandler(cb_time_date, pattern=r'^time_date\|')],
        TIME_CHOOSE_HOUR: [
            CallbackQueryHandler(cb_time_hour, pattern=r'^time_hour\|'),
            CallbackQueryHandler(cb_time_confirm, pattern=r'^time_confirm$'),
        ],
    },
    fallbacks=[CallbackQueryHandler(cmd_start, pattern='^back$')],
)

application.add_handler(conv_start)
application.add_handler(conv_time)
application.add_handler(CommandHandler("register", reg_start))
application.add_handler(CallbackQueryHandler(cb_date, pattern=r'^date\|'))
application.add_handler(CallbackQueryHandler(cb_book, pattern=r'^book\|'))
application.add_handler(CallbackQueryHandler(cb_time_repeat, pattern=r'^time_repeat$'))

# --- Flask + Application запуск
def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
