import os
import json
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
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

# --- Flask healthcheck
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# --- States
REG_NAME, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO = range(5)
SLOT_DATE, SLOT_TIME = range(5,7)
CHOOSING_REGION, CHOOSING_FIELD, CHOOSING_SPEC, CHOOSING_DATE, CHOOSING_TIME = range(7,12)

# --- Служебные функции
def get_specialists():
    rows = ws.get_all_records()
    return [row for row in rows if row.get('ФИО')]

def get_fields(region):
    rows = get_specialists()
    return sorted(set(row['Сфера'] for row in rows if row['Город'] == region and row['Сфера']))

def get_regions():
    rows = get_specialists()
    return sorted(set(row['Город'] for row in rows if row['Город']))

def get_specialist_row(telegram_id):
    rows = ws.get_all_records()
    for idx, row in enumerate(rows, start=2):  # c учётом заголовков
        if str(row.get('Telegram ID')) == str(telegram_id):
            return ws, idx, row
    return ws, None, None

def update_slots(ws, row_num, slots):
    ws.update_cell(row_num, 8, ';'.join(slots))  # 8 — колонка H

# --- /register
async def reg_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите ФИО:")
    return REG_NAME

async def reg_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['fio'] = update.message.text.strip()
    await update.message.reply_text("Введите город:")
    return REG_CITY

async def reg_city(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['city'] = update.message.text.strip()
    await update.message.reply_text("Введите сферу деятельности:")
    return REG_FIELD

async def reg_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['field'] = update.message.text.strip()
    await update.message.reply_text("Опишите себя (1-2 предложения):")
    return REG_DESC

async def reg_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['desc'] = update.message.text.strip()
    await update.message.reply_text("Пришлите фото сертификата:")
    return REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    file_id = update.message.photo[-1].file_id if update.message.photo else ''
    data = ctx.user_data
    row = [
        data['fio'],
        data['city'],
        data['field'],
        data['desc'],
        file_id,
        update.effective_user.id,
        update.effective_user.username or ''
    ]
    ws.append_row(row)
    await update.message.reply_text("Спасибо, вы зарегистрированы как специалист!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отмена регистрации.")
    return ConversationHandler.END

# --- /addslot
async def addslot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Выберите дату для записи:", reply_markup=date_kb())
    return SLOT_DATE

def date_kb():
    from datetime import date, timedelta
    kb = []
    for i in range(5):
        d = (date.today() + timedelta(days=i)).strftime('%Y-%m-%d')
        kb.append([InlineKeyboardButton(d, callback_data=f"slotdate_{d}")])
    return InlineKeyboardMarkup(kb)

async def addslot_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    date = update.callback_query.data.split('_')[1]
    ctx.user_data['slot_date'] = date
    kb = []
    for hour in range(10, 19):
        kb.append([InlineKeyboardButton(f"{hour:02d}:00", callback_data=f"slottime_{hour:02d}:00")])
    kb.append([InlineKeyboardButton("Подтвердить", callback_data="slotconfirm")])
    await update.callback_query.edit_message_text(
        f"Дата: {date}\nВыберите время (можно несколько):",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    ctx.user_data['slot_times'] = []
    return SLOT_TIME

async def addslot_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    if data.startswith("slottime_"):
        time = data.split('_')[1]
        if time not in ctx.user_data['slot_times']:
            ctx.user_data['slot_times'].append(time)
        kb = []
        for hour in range(10, 19):
            mark = "✅" if f"{hour:02d}:00" in ctx.user_data['slot_times'] else ""
            kb.append([InlineKeyboardButton(f"{hour:02d}:00 {mark}", callback_data=f"slottime_{hour:02d}:00")])
        kb.append([InlineKeyboardButton("Подтвердить", callback_data="slotconfirm")])
        await update.callback_query.edit_message_text(
            f"Дата: {ctx.user_data['slot_date']}\nВыбрано: {', '.join(ctx.user_data['slot_times']) or 'ничего'}",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return SLOT_TIME
    elif data == "slotconfirm":
        ws, row_num, _ = get_specialist_row(update.effective_user.id)
        if row_num:
            # Сохраняем слоты
            slots_str = ws.cell(row_num, 8).value or ""
            slots = [s for s in slots_str.split(';') if s]
            date = ctx.user_data['slot_date']
            for t in ctx.user_data['slot_times']:
                slot = f"{date} {t}"
                if slot not in slots:
                    slots.append(slot)
            update_slots(ws, row_num, slots)
            await update.callback_query.edit_message_text(
                f"Добавлено: {', '.join(ctx.user_data['slot_times'])}\n\nДобавить ещё?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Да, выбрать другую дату", callback_data="slotaddmore")],
                    [InlineKeyboardButton("Завершить", callback_data="slotend")]
                ])
            )
            return SLOT_DATE
        else:
            await update.callback_query.edit_message_text("Ваша анкета не найдена.")
            return ConversationHandler.END
    elif data == "slotaddmore":
        await update.callback_query.edit_message_text("Выберите дату для записи:", reply_markup=date_kb())
        return SLOT_DATE
    elif data == "slotend":
        await update.callback_query.edit_message_text("Добавление слотов завершено.")
        return ConversationHandler.END

# --- /start (запись на консультацию)
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    regions = get_regions()
    kb = [[InlineKeyboardButton(r, callback_data=f"region_{r}")] for r in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data.split('_')[1]
    ctx.user_data['region'] = region
    fields = get_fields(region)
    kb = [[InlineKeyboardButton(f, callback_data=f"field_{f}")] for f in fields]
    kb.append([InlineKeyboardButton("Назад", callback_data="start_back")])
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nВыберите сферу:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = update.callback_query.data.split('_')[1]
    ctx.user_data['field'] = field
    specs = [row for row in get_specialists() if row['Город']==ctx.user_data['region'] and row['Сфера']==field]
    ctx.user_data['specs'] = {str(row['Telegram ID']): row for row in specs}
    kb = [[InlineKeyboardButton(row['ФИО'], callback_data=f"spec_{row['Telegram ID']}")] for row in specs]
    kb.append([InlineKeyboardButton("Назад", callback_data="region_back")])
    await update.callback_query.edit_message_text(
        f"Регион: {ctx.user_data['region']}\nСфера: {field}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    spec_id = update.callback_query.data.split('_')[1]
    spec = ctx.user_data['specs'][spec_id]
    # Достаём слоты (и показываем только доступные, не занятые)
    ws, row_num, _ = get_specialist_row(spec_id)
    slots_str = ws.cell(row_num, 8).value or ""
    slots = [s for s in slots_str.split(';') if s]
    # Фильтруем по дате: показываем только ближайшие 7 дней
    from datetime import datetime, timedelta
    now = datetime.now()
    slots = [s for s in slots if now <= datetime.strptime(s, "%Y-%m-%d %H:%M") <= now + timedelta(days=7)]
    kb = [[InlineKeyboardButton(s, callback_data=f"slot_{spec_id}_{s}")] for s in slots]
    kb.append([InlineKeyboardButton("Назад", callback_data="field_back")])
    caption = f"{spec['ФИО']}\n{spec['Описание']}"
    if spec['photo_file_id']:
        await update.callback_query.message.reply_photo(
            photo=spec['photo_file_id'], caption=caption, reply_markup=InlineKeyboardMarkup(kb)
        )
        await update.callback_query.delete_message()
    else:
        await update.callback_query.edit_message_text(caption, reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['current_spec_id'] = spec_id
    return CHOOSING_DATE

async def cb_slot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, spec_id, slot = update.callback_query.data.split('_', 2)
    ws, row_num, _ = get_specialist_row(spec_id)
    slots_str = ws.cell(row_num, 8).value or ""
    slots = [s for s in slots_str.split(';') if s]
    if slot not in slots:
        await update.callback_query.edit_message_text("Это время уже занято.")
        return ConversationHandler.END
    # Бронируем (удаляем слот)
    slots.remove(slot)
    update_slots(ws, row_num, slots)
    # Уведомление эксперту
    specialist = ws.row_values(row_num)
    expert_id = int(specialist[5])
    client = update.effective_user
    try:
        await update.get_bot().send_message(
            chat_id=expert_id,
            text=f"У вас новая запись: {client.full_name} (@{client.username or '-'}) на {slot}"
        )
    except Exception:
        pass
    await update.callback_query.edit_message_text(
        f"Вы записались к специалисту: {specialist[0]} на {slot}"
    )
    return ConversationHandler.END

# --- Назад (универсальный хендлер)
async def cb_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data == "start_back":
        return await cmd_start(update, ctx)
    if data == "region_back":
        return await cb_region(update, ctx)
    if data == "field_back":
        return await cb_field(update, ctx)

# --- Handlers
application = ApplicationBuilder().token(TOKEN).build()

# /register
application.add_handler(ConversationHandler(
    entry_points=[CommandHandler("register", reg_start)],
    states={
        REG_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
        REG_CITY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)],
        REG_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field)],
        REG_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_PHOTO: [MessageHandler(filters.PHOTO, reg_photo)],
    },
    fallbacks=[CommandHandler("cancel", reg_cancel)],
))

# /addslot
application.add_handler(ConversationHandler(
    entry_points=[CommandHandler("addslot", addslot_start)],
    states={
        SLOT_DATE: [CallbackQueryHandler(addslot_date, pattern="^slotdate_")],
        SLOT_TIME: [CallbackQueryHandler(addslot_time, pattern="^(slottime_|slotconfirm|slotaddmore|slotend)")],
    },
    fallbacks=[],
))

# /start
application.add_handler(ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CHOOSING_REGION: [CallbackQueryHandler(cb_region, pattern="^region_")],
        CHOOSING_FIELD: [CallbackQueryHandler(cb_field, pattern="^field_"), CallbackQueryHandler(cb_back, pattern="^start_back$")],
        CHOOSING_SPEC: [CallbackQueryHandler(cb_spec, pattern="^spec_"), CallbackQueryHandler(cb_back, pattern="^region_back$")],
        CHOOSING_DATE: [CallbackQueryHandler(cb_slot, pattern="^slot_"), CallbackQueryHandler(cb_back, pattern="^field_back$")],
    },
    fallbacks=[],
))

# --- Для Render
def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
