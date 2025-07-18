import os
import json
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
from datetime import datetime, timedelta

# ========== Логирование и переменные окружения ==========
logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])

# ========== Подключение к Google Sheets ==========
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds  = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc     = gspread.authorize(creds)
sheet  = gc.open_by_key(SHEET_ID)

# ========== Утилиты для работы со специалистами ==========
def get_specialists():
    ws = sheet.worksheet('Лист1')
    recs = ws.get_all_records()
    specs = []
    for r in recs:
        specs.append({
            'fio':           r.get('ФИО эксперта', ''),
            'city':          r.get('город эксперта', ''),
            'sphere':        r.get('сфера', ''),
            'description':   r.get('описание', ''),
            'photo_file_id': r.get('photo_file_id', ''),
            'telegram_id':   r.get('Telegram ID'),
            'slots':         [s.strip() for s in r.get('Slots', '').split(';') if s.strip()]
        })
    return specs

def get_specialist_row(telegram_id):
    ws = sheet.worksheet('Лист1')
    recs = ws.get_all_records()
    for idx, r in enumerate(recs, start=2):
        if str(r.get('Telegram ID')) == str(telegram_id):
            return ws, idx, r
    return None, None, None

def add_slots_for_specialist(telegram_id, date, times):
    ws_obj, row, _ = get_specialist_row(telegram_id)
    if not row:
        return False
    cur = ws_obj.cell(row, 9).value or ''  # коло́нка I — Slots
    lst = [s.strip() for s in cur.split(';') if s.strip()]
    for t in times:
        slot = f"{date} {t}"
        if slot not in lst:
            lst.append(slot)
    ws_obj.update_cell(row, 9, ';'.join(sorted(lst)))
    return True

# ========== Conversation: регистрация эксперта ==========
REG_NAME, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO = range(5)

async def reg_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите ваше ФИО:")
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
    await update.message.reply_text("Кратко об опыте:")
    return REG_DESC

async def reg_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['desc'] = update.message.text
    await update.message.reply_text("Пришлите фото сертификата:")
    return REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    file_id = update.message.photo[-1].file_id if update.message.photo else ''
    ws = sheet.worksheet('Лист1')
    ws.append_row([
        datetime.now().isoformat(),
        ctx.user_data['fio'],
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        file_id,
        update.effective_user.id,
        update.effective_user.username or '',
        ""  # пустой Slots
    ])
    await update.message.reply_text("✔ Вы зарегистрированы как эксперт!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Регистрация отменена.")
    return ConversationHandler.END

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

# ========== Conversation: добавление слотов (/time) ==========
TIME_DATE, TIME_SELECT = range(2)

async def time_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    dates = [(datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    kb = [[InlineKeyboardButton(d, callback_data=f"date_{d}")] for d in dates]
    await update.message.reply_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['slot_times'] = []
    return TIME_DATE

async def time_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    date = q.data.split("_",1)[1]
    ctx.user_data['slot_date'] = date
    slots = [f"{h:02d}:00" for h in range(9,19)]
    kb = [[InlineKeyboardButton(
        f"{'✅ ' if t in ctx.user_data['slot_times'] else ''}{t}",
        callback_data=f"tselect_{t}"
    )] for t in slots]
    kb += [[InlineKeyboardButton("Готово", callback_data="tconfirm")]]
    await q.message.edit_text(f"Дата: {date}\nВыберите время:", reply_markup=InlineKeyboardMarkup(kb))
    return TIME_SELECT

async def time_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = q.data
    if data.startswith("tselect_"):
        t = data.split("_",1)[1]
        lst = ctx.user_data['slot_times']
        if t in lst: lst.remove(t)
        else:       lst.append(t)
        return await time_date(update, ctx)
    if data == "tconfirm":
        ok = add_slots_for_specialist(
            q.from_user.id,
            ctx.user_data['slot_date'],
            ctx.user_data['slot_times']
        )
        await q.message.reply_text(
            "Слоты добавлены!" if ok else "Ошибка: вы не эксперт."
        )
        return ConversationHandler.END
    return TIME_SELECT

conv_time = ConversationHandler(
    entry_points=[CommandHandler("time", time_start)],
    states={
        TIME_DATE:   [CallbackQueryHandler(time_date,   pattern=r"^date_")],
        TIME_SELECT: [CallbackQueryHandler(time_select, pattern=r"^(tselect_|tconfirm)")]
    },
    fallbacks=[]
)

# ========== Консультации: выбор региона/эксперта/даты/времени ==========
CHOOSING_REGION, CHOOSING_FIELD, CHOOSING_SPEC, CHOOSING_DATE, CHOOSING_TIME = range(5)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer() if update.callback_query else None
    specs   = get_specialists()
    regions = sorted({sp['city'] for sp in specs})
    kb      = [[InlineKeyboardButton(r, callback_data=f"region_{r}")] for r in regions]
    target  = update.callback_query.message if update.callback_query else update.message
    await target.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['specialists'] = specs
    return CHOOSING_REGION

# … сюда вставьте ваши cb_region, cb_field, cb_spec, cb_date, cb_time из existing кода …

# ========== Новый /start с двумя кнопками ==========
async def start_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📋 Консультации",         callback_data="consult")],
        [InlineKeyboardButton("🖊 Регистрация эксперта", callback_data="register")]
    ]
    await update.message.reply_text(
        "Привет! Выберите действие:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def cb_consult(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await cmd_start(update, ctx)

async def cb_register_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await reg_start(update, ctx)

# ========== Сборка и запуск бота ==========
application = ApplicationBuilder().token(TOKEN).build()

# заменяем предыдущий /start
application.add_handler(CommandHandler("start", start_command))
application.add_handler(CallbackQueryHandler(cb_consult,        pattern="^consult$"))
application.add_handler(CallbackQueryHandler(cb_register_button, pattern="^register$"))

# старые handlers
application.add_handler(conv_reg)
application.add_handler(conv_time)
application.add_handler(ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CHOOSING_REGION: [CallbackQueryHandler(cb_region, pattern=r"^region_")],
        CHOOSING_FIELD:  [CallbackQueryHandler(cb_field,  pattern=r"^field_")],
        CHOOSING_SPEC:   [CallbackQueryHandler(cb_spec,   pattern=r"^spec_")],
        CHOOSING_DATE:   [CallbackQueryHandler(cb_date,   pattern=r"^date_")],
        CHOOSING_TIME:   [CallbackQueryHandler(cb_time,   pattern=r"^time_")]
    },
    fallbacks=[]
))

if __name__ == "__main__":
    application.run_polling()
