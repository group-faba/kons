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
from datetime import datetime

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
sheet = spreadsheet.worksheet('Лист1')

# --- Flask healthcheck (чтобы Render не засыпал)
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

# --- Вспомогательные функции
def get_regions():
    rows = sheet.get_all_records()
    return sorted(list(set(row["Город"] for row in rows if row["Город"])))

def get_fields(region):
    rows = sheet.get_all_records()
    return sorted(list(set(row["Сфера"] for row in rows if row["Город"] == region)))

def get_specialists(region, field):
    rows = sheet.get_all_records()
    return [row for row in rows if row["Город"] == region and row["Сфера"] == field]

def get_slots(row):
    # Читаем все слоты из строк H,J и т.д. (H - 8-я колонка)
    slots = []
    for col in range(8, sheet.col_count+1):
        val = row.get(sheet.cell(1, col).value, "")
        if val:
            slots += val.split(";")
    # В одну строку (старые данные) может быть все через ;, новые можно хранить по разным датам в новых колонках
    res = []
    for slot in slots:
        slot = slot.strip()
        if slot:
            if " " in slot:
                res.append(slot)
    return sorted(res)

def set_slots(row_idx, new_slots):
    slots_str = ";".join(new_slots)
    sheet.update_cell(row_idx+2, 8, slots_str)

def find_expert_row(fio, telegram_id):
    rows = sheet.get_all_records()
    for i, row in enumerate(rows):
        if row['ФИО'] == fio and str(row['Telegram ID']) == str(telegram_id):
            return i, row
    return None, None

# --- Conversation states
START_REGION, START_FIELD, START_SPEC, START_CONFIRM, START_TIME = range(5)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    regions = get_regions()
    kb = [[InlineKeyboardButton(region, callback_data=f"region:{region}")] for region in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return START_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data.split(":",1)[1]
    ctx.user_data["region"] = region
    fields = get_fields(region)
    kb = [[InlineKeyboardButton(field, callback_data=f"field:{field}")] for field in fields]
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nВыберите сферу:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return START_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = update.callback_query.data.split(":",1)[1]
    ctx.user_data["field"] = field
    region = ctx.user_data["region"]
    specs = get_specialists(region, field)
    if not specs:
        await update.callback_query.edit_message_text("Нет специалистов в этом регионе/сфере.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(spec["ФИО"], callback_data=f"spec:{i}")] for i, spec in enumerate(specs)]
    ctx.user_data["specs"] = specs
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nСфера: {field}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return START_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    idx = int(update.callback_query.data.split(":",1)[1])
    spec = ctx.user_data["specs"][idx]
    fio = spec["ФИО"]
    desc = spec["Описание"]
    photo = spec.get("photo_file_id", None)
    ctx.user_data["chosen_spec"] = spec

    # Получить доступные слоты
    slots = []
    all_slots = get_slots(spec)
    now = datetime.now()
    for slot in all_slots:
        try:
            dt = datetime.strptime(slot, "%d.%m.%Y %H:%M")
            if dt >= now:
                slots.append(slot)
        except Exception:
            continue

    caption = f"{fio}\n{desc}"
    kb = [
        [InlineKeyboardButton("Назад", callback_data="back")],
    ]
    if slots:
        kb.append([InlineKeyboardButton("Выбрать этого специалиста", callback_data="choose")])
    if photo:
        await update.callback_query.message.reply_photo(
            photo=photo, caption=caption, reply_markup=InlineKeyboardMarkup(kb)
        )
        await update.callback_query.delete_message()
    else:
        await update.callback_query.edit_message_text(caption, reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data["slots"] = slots
    return START_CONFIRM

async def cb_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = ctx.user_data["field"]
    region = ctx.user_data["region"]
    specs = ctx.user_data["specs"]
    kb = [[InlineKeyboardButton(spec["ФИО"], callback_data=f"spec:{i}")] for i, spec in enumerate(specs)]
    await update.callback_query.message.reply_text(
        f"Регион: {region}\nСфера: {field}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    await update.callback_query.delete_message()
    return START_SPEC

async def cb_choose(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    spec = ctx.user_data["chosen_spec"]
    slots = ctx.user_data["slots"]
    if not slots:
        await update.callback_query.message.reply_text("Нет свободного времени для записи.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(slot, callback_data=f"time:{i}")] for i, slot in enumerate(slots)]
    await update.callback_query.message.reply_text(
        "Выберите время для записи:", reply_markup=InlineKeyboardMarkup(kb)
    )
    await update.callback_query.delete_message()
    return START_TIME

async def cb_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    idx = int(update.callback_query.data.split(":",1)[1])
    spec = ctx.user_data["chosen_spec"]
    slots = ctx.user_data["slots"]
    slot = slots[idx]
    fio = spec["ФИО"]
    telegram_id = spec["Telegram ID"]

    # --- Проверка: слот еще свободен?
    # Повторно читаем (мог кто-то занять)
    _, real_spec = find_expert_row(fio, telegram_id)
    real_slots = get_slots(real_spec)
    if slot not in real_slots:
        await update.callback_query.edit_message_text("Это время уже занято. Попробуйте другое.")
        return ConversationHandler.END

    # --- Бронируем: убираем слот у эксперта
    row_idx, _ = find_expert_row(fio, telegram_id)
    new_slots = [s for s in real_slots if s != slot]
    set_slots(row_idx, new_slots)

    # --- Уведомление эксперту
    app = update.get_bot()
    try:
        await app.send_message(
            chat_id=telegram_id,
            text=f"Новая запись: {update.effective_user.full_name} на {slot}"
        )
    except Exception as e:
        logging.error(f"Ошибка отправки уведомления эксперту: {e}")

    await update.callback_query.edit_message_text(
        f"Вы записались к специалисту: {fio} на {slot}"
    )
    return ConversationHandler.END

# --- Регистрация специалиста
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
    # Сохраняем анкету
    fio = ctx.user_data['fio']
    row = [
        fio,
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        ctx.user_data['photo_file_id'],
        update.effective_user.id,
        update.effective_user.username or ''
    ]
    sheet.append_row(row)
    await update.message.reply_text("Спасибо, вы зарегистрированы как специалист!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отмена регистрации.")
    return ConversationHandler.END

# --- Добавление слота экспертом
ADDSLOT_DATE, ADDSLOT_TIME = range(2)
async def addslot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите дату для записи (например, 26.06.2025):")
    return ADDSLOT_DATE

async def addslot_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["slot_date"] = update.message.text
    await update.message.reply_text("Введите время (например, 15:00 или несколько через запятую: 10:00,11:00):")
    return ADDSLOT_TIME

async def addslot_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    times = update.message.text.replace(" ", "").split(",")
    date = ctx.user_data["slot_date"]
    slots = [f"{date} {t}" for t in times if t]
    # Найти свою строку
    fio = update.effective_user.full_name
    telegram_id = update.effective_user.id
    row_idx, row = find_expert_row(fio, telegram_id)
    if row_idx is None:
        await update.message.reply_text("Ваша анкета не найдена.")
        return ConversationHandler.END
    # Добавить к существующим слотам
    current_slots = get_slots(row)
    all_slots = current_slots + slots
    set_slots(row_idx, all_slots)
    await update.message.reply_text("Время для записи добавлено!")
    return ConversationHandler.END

# --- Handlers
application = ApplicationBuilder().token(TOKEN).build()

conv_main = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        START_REGION: [CallbackQueryHandler(cb_region, pattern="^region:")],
        START_FIELD:  [CallbackQueryHandler(cb_field, pattern="^field:")],
        START_SPEC:   [CallbackQueryHandler(cb_spec, pattern="^spec:"), CallbackQueryHandler(cb_back, pattern="^back$")],
        START_CONFIRM:[CallbackQueryHandler(cb_choose, pattern="^choose$"), CallbackQueryHandler(cb_back, pattern="^back$")],
        START_TIME:   [CallbackQueryHandler(cb_time, pattern="^time:")],
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
    fallbacks=[CommandHandler("cancel", reg_cancel)],
)

conv_addslot = ConversationHandler(
    entry_points=[CommandHandler("addslot", addslot_start)],
    states={
        ADDSLOT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addslot_date)],
        ADDSLOT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addslot_time)],
    },
    fallbacks=[],
)

application.add_handler(conv_main)
application.add_handler(conv_reg)
application.add_handler(conv_addslot)

# --- Запуск
def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
