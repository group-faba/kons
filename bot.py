import os
import json
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta

from flask import Flask
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# --- Google Sheets
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SHEET_ID)
ws = spreadsheet.worksheet('Лист1')

def get_main_records():
    """Чтение всех анкет специалистов из Лист1"""
    records = ws.get_all_records()
    return records

def add_slot_to_sheet(fio, date, times):
    """Добавляет слоты в таблицу по ФИО"""
    records = ws.get_all_records()
    for i, row in enumerate(records, start=2):
        if row['ФИО'] == fio:
            current_slots = row.get('Слоты', "")
            slot_str = f"{date} {';'.join(times)}"
            ws.update_cell(i, 8, f"{current_slots}|{slot_str}" if current_slots else slot_str)
            return True
    return False

# --- Flask healthcheck
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# --- /register (регистрация специалиста)
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
    await update.message.reply_text("Опишите себя в паре предложений:")
    return REG_DESC

async def reg_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['desc'] = update.message.text
    await update.message.reply_text("Пришлите фото сертификата:")
    return REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    photo_id = update.message.photo[-1].file_id if update.message.photo else ""
    ctx.user_data['photo_file_id'] = photo_id
    ws.append_row([
        ctx.user_data['fio'],
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        ctx.user_data['photo_file_id'],
        update.effective_user.id,
        update.effective_user.username or '',
        ""  # Слоты
    ])
    await update.message.reply_text("Вы зарегистрированы как специалист!")
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
    fallbacks=[],
)

# --- /addslot (добавление слотов)
ADD_SLOT_DATE, ADD_SLOT_TIME, ADD_SLOT_CONFIRM = range(3)

def get_time_buttons():
    # Пример: 10:00 до 18:00, шаг 1 час
    buttons = []
    for hour in range(10, 19):
        t = f"{hour:02}:00"
        buttons.append([InlineKeyboardButton(t, callback_data=t)])
    return buttons

async def addslot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # ищем ФИО по user_id
    records = ws.get_all_records()
    fio = None
    for row in records:
        if str(row.get("Telegram ID", "")) == str(user_id):
            fio = row["ФИО"]
            break
    if not fio:
        await update.message.reply_text("Вы не зарегистрированы. Пройдите /register.")
        return ConversationHandler.END
    ctx.user_data['fio'] = fio
    # Кнопки дат: сегодня, завтра
    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)
    kb = [
        [InlineKeyboardButton(today.strftime("%d.%m.%Y"), callback_data=today.strftime("%d.%m.%Y"))],
        [InlineKeyboardButton(tomorrow.strftime("%d.%m.%Y"), callback_data=tomorrow.strftime("%d.%m.%Y"))],
    ]
    await update.message.reply_text("Выберите дату для слота:", reply_markup=InlineKeyboardMarkup(kb))
    return ADD_SLOT_DATE

async def addslot_choose_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    ctx.user_data['slot_date'] = update.callback_query.data
    # Кнопки времени
    await update.callback_query.edit_message_text(
        f"Дата: {ctx.user_data['slot_date']}\nВыберите время (можно несколько):",
        reply_markup=InlineKeyboardMarkup(get_time_buttons() + [[InlineKeyboardButton("Подтвердить", callback_data="confirm")]])
    )
    ctx.user_data['slot_times'] = []
    return ADD_SLOT_TIME

async def addslot_choose_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    await update.callback_query.answer()
    if data == "confirm":
        if not ctx.user_data['slot_times']:
            await update.callback_query.answer("Выберите хотя бы одно время.", show_alert=True)
            return ADD_SLOT_TIME
        # Сохраняем в таблицу
        add_slot_to_sheet(ctx.user_data['fio'], ctx.user_data['slot_date'], ctx.user_data['slot_times'])
        await update.callback_query.edit_message_text(
            f"Добавлено:\n{ctx.user_data['slot_date']} {', '.join(ctx.user_data['slot_times'])}"
        )
        return ConversationHandler.END
    else:
        # Добавление времени в список (без дублей)
        if data not in ctx.user_data['slot_times']:
            ctx.user_data['slot_times'].append(data)
        await update.callback_query.answer(f"Выбрано: {', '.join(ctx.user_data['slot_times'])}")
        return ADD_SLOT_TIME

conv_addslot = ConversationHandler(
    entry_points=[CommandHandler("addslot", addslot_start)],
    states={
        ADD_SLOT_DATE: [CallbackQueryHandler(addslot_choose_date)],
        ADD_SLOT_TIME: [CallbackQueryHandler(addslot_choose_time)],
    },
    fallbacks=[],
)

# --- /start (клиент выбирает регион → сферу → специалиста)
CHOICE_REGION, CHOICE_FIELD, CHOICE_SPEC, SHOW_SPEC = range(4)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    records = get_main_records()
    if not records:
        await update.message.reply_text("Нет доступных специалистов.")
        return ConversationHandler.END
    regions = sorted(set(r["Город"] for r in records if r["Город"]))
    kb = [[InlineKeyboardButton(region, callback_data=region)] for region in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_REGION

async def cb_choose_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    ctx.user_data['region'] = update.callback_query.data
    records = get_main_records()
    fields = sorted(set(r["Сфера"] for r in records if r["Город"] == ctx.user_data['region']))
    kb = [[InlineKeyboardButton(field, callback_data=field)] for field in fields]
    await update.callback_query.edit_message_text(
        f"Регион: {ctx.user_data['region']}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOICE_FIELD

async def cb_choose_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    ctx.user_data['field'] = update.callback_query.data
    records = get_main_records()
    specs = [r for r in records if r["Город"] == ctx.user_data['region'] and r["Сфера"] == ctx.user_data['field']]
    ctx.user_data['specs'] = specs
    kb = [[InlineKeyboardButton(s["ФИО"], callback_data=str(i))] for i, s in enumerate(specs)]
    await update.callback_query.edit_message_text(
        f"Регион: {ctx.user_data['region']}\nСфера: {ctx.user_data['field']}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOICE_SPEC

async def cb_choose_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    idx = int(update.callback_query.data)
    spec = ctx.user_data['specs'][idx]
    ctx.user_data['chosen_spec'] = spec
    kb = [
        [InlineKeyboardButton("Назад", callback_data="back")],
        [InlineKeyboardButton("Выбрать этого специалиста", callback_data="choose_this")]
    ]
    caption = f"{spec['ФИО']}\n{spec['Описание']}"
    if spec["photo_file_id"]:
        await update.callback_query.message.reply_photo(
            photo=spec["photo_file_id"],
            caption=caption,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        await update.callback_query.delete_message()
    else:
        await update.callback_query.edit_message_text(
            caption, reply_markup=InlineKeyboardMarkup(kb)
        )
    return SHOW_SPEC

async def cb_spec_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    specs = ctx.user_data['specs']
    kb = [[InlineKeyboardButton(s["ФИО"], callback_data=str(i))] for i, s in enumerate(specs)]
    await update.callback_query.message.reply_text(
        "Выберите специалиста:", reply_markup=InlineKeyboardMarkup(kb)
    )
    await update.callback_query.delete_message()
    return CHOICE_SPEC

async def cb_choose_this(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    spec = ctx.user_data['chosen_spec']
    await update.callback_query.message.reply_text(
        f"Вы записались к специалисту: {spec['ФИО']}"
    )
    await update.callback_query.delete_message()
    return ConversationHandler.END

conv_main = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CHOICE_REGION: [CallbackQueryHandler(cb_choose_region)],
        CHOICE_FIELD:  [CallbackQueryHandler(cb_choose_field)],
        CHOICE_SPEC:   [CallbackQueryHandler(cb_choose_spec)],
        SHOW_SPEC: [
            CallbackQueryHandler(cb_spec_back, pattern="^back$"),
            CallbackQueryHandler(cb_choose_this, pattern="^choose_this$"),
        ],
    },
    fallbacks=[],
)

application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(conv_reg)
application.add_handler(conv_addslot)
application.add_handler(conv_main)

if __name__ == "__main__":
    import threading
    threading.Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": PORT}, daemon=True).start()
    application.run_polling()
