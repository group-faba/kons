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

logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT       = int(os.environ.get('PORT', '8080'))

SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SHEET_ID)
sheet = spreadsheet.worksheet("Лист1")

def get_expert_row(telegram_id):
    vals = sheet.get_all_records()
    for i, row in enumerate(vals, start=2):
        if str(row.get("Telegram ID")) == str(telegram_id):
            return i, row
    return None, None

def get_expert_by_id(telegram_id):
    _, row = get_expert_row(telegram_id)
    return row

def get_expert_slots(row):
    # H = 8 (indexing from 1), slot string формат: 2025-06-25 13:00;2025-06-26 15:00
    slots_str = row.get('', '')  # колонка H не имеет заголовка, если имеет — исправь на get('your_colname')
    slots_str = row.get("time", slots_str)
    if not slots_str:
        return []
    return sorted(slots_str.split(';'))

def set_expert_slots(telegram_id, slots):
    row_num, _ = get_expert_row(telegram_id)
    if not row_num:
        return False
    sheet.update_cell(row_num, 8, ';'.join(slots))  # H = 8
    return True

def get_all_experts():
    vals = sheet.get_all_records()
    return vals

# --- Flask healthcheck
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# --- Register conversation
REG_NAME, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO, REG_PHOTO2 = range(6)
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
    await update.message.reply_text("Пришлите фото сертификата или любой документ:")
    return REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['photo_file_id'] = update.message.photo[-1].file_id if update.message.photo else ''
    await update.message.reply_text("Пришлите свою фотографию (эксперт фото):")
    return REG_PHOTO2

async def reg_photo2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    expert_photo_file_id = update.message.photo[-1].file_id if update.message.photo else ''
    fio = ctx.user_data['fio']
    sheet.append_row([
        fio,
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        ctx.user_data['photo_file_id'],
        update.effective_user.id,
        update.effective_user.username or '',
        expert_photo_file_id  # новый столбец
    ])
    await update.message.reply_text("Спасибо, вы зарегистрированы как специалист!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отмена регистрации.")
    return ConversationHandler.END

# --- ADD SLOT (создание расписания)
ADDSLOT_DATE, ADDSLOT_TIME = range(2)
async def addslot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите дату для записи (например, 2025-06-26):")
    return ADDSLOT_DATE

async def addslot_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['slot_date'] = update.message.text.strip()
    await update.message.reply_text("Введите время (через запятую, например: 10:00, 13:00, 16:00):")
    return ADDSLOT_TIME

async def addslot_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    times = [t.strip() for t in update.message.text.split(',')]
    date = ctx.user_data['slot_date']
    full_slots = []
    row_num, row = get_expert_row(update.effective_user.id)
    prev_slots = []
    if row:
        slots_str = row.get("time", row.get('', ''))
        if slots_str:
            prev_slots = [s for s in slots_str.split(';') if s]
    for t in times:
        dt = f"{date} {t}"
        if dt not in prev_slots:
            prev_slots.append(dt)
    set_expert_slots(update.effective_user.id, prev_slots)
    await update.message.reply_text(
        f"Время добавлено: {date} — {', '.join(times)}\n\n"
        "Выставить слоты на другой день / изменить? (/addslot)\nЗавершить — /start"
    )
    return ConversationHandler.END

# --- Main flow: /start (регион/сфера/эксперт/запись)
CHOOSING_REGION, CHOOSING_FIELD, CHOOSING_SPEC, CHOOSING_DATE, CHOOSING_TIME = range(5)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Регион (города)
    experts = get_all_experts()
    cities = sorted({e['Город'] for e in experts if e['Город']})
    kb = [[InlineKeyboardButton(city, callback_data=city)] for city in cities]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data['region'] = q.data
    experts = get_all_experts()
    fields = sorted({e['Сфера'] for e in experts if e['Город'] == q.data})
    kb = [[InlineKeyboardButton(field, callback_data=field)] for field in fields]
    kb.append([InlineKeyboardButton("Назад", callback_data='back_region')])
    await q.edit_message_text(
        f"Регион: {q.data}\nВыберите сферу:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data['field'] = q.data
    experts = get_all_experts()
    specs = [e for e in experts if e['Город'] == ctx.user_data['region'] and e['Сфера'] == q.data]
    ctx.user_data['specs'] = {str(i): e for i, e in enumerate(specs)}
    kb = [[InlineKeyboardButton(e['ФИО'], callback_data=f"spec_{i}")]
          for i, e in ctx.user_data['specs'].items()]
    kb.append([InlineKeyboardButton("Назад", callback_data='back_field')])
    await q.edit_message_text(
        f"Регион: {ctx.user_data['region']}\nСфера: {q.data}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_SPEC

async def cb_back_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    return await cmd_start(update, ctx)

async def cb_back_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    return await cb_region(update, ctx)

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    spec_id = q.data.split('_')[1]
    spec = ctx.user_data['specs'][spec_id]
    ctx.user_data['selected_spec'] = spec
    slots_str = spec.get("time", spec.get('', ''))
    slots = [s for s in slots_str.split(';') if s]
    dates = sorted(set(s.split()[0] for s in slots))
    ctx.user_data['spec_dates'] = dates
    photo_id = spec.get('expert_photo_file_id') or spec.get('photo_file_id', '')
    kb = [[InlineKeyboardButton(date, callback_data=f"date_{date}")]
          for date in dates]
    kb.append([InlineKeyboardButton("Назад", callback_data='back_spec')])
    text = f"{spec['ФИО']}\n{spec['Описание']}"
    if photo_id:
        await q.message.reply_photo(
            photo=photo_id,
            caption=text,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        await q.delete_message()
    else:
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_DATE

async def cb_back_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    return await cb_field(update, ctx)

async def cb_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    date = q.data.split('_')[1]
    ctx.user_data['selected_date'] = date
    spec = ctx.user_data['selected_spec']
    slots_str = spec.get("time", spec.get('', ''))
    slots = [s for s in slots_str.split(';') if s and s.startswith(date)]
    kb = [[InlineKeyboardButton(s, callback_data=f"time_{s}")] for s in slots]
    kb.append([InlineKeyboardButton("Назад", callback_data='back_date')])
    await q.message.reply_text(
        f"Доступное время на {date}:", reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_TIME

async def cb_back_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    return await cb_spec(update, ctx)

async def cb_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    slot = q.data.split('_', 1)[1]
    spec = ctx.user_data['selected_spec']
    # Проверка: убрать слот
    row_num, row = get_expert_row(spec['Telegram ID'])
    if not row_num:
        await q.message.reply_text("Ошибка! Эксперт не найден.")
        return ConversationHandler.END
    slots_str = row.get("time", row.get('', ''))
    slots = [s for s in slots_str.split(';') if s]
    if slot not in slots:
        await q.message.reply_text("Это время уже занято.")
        return ConversationHandler.END
    # Убираем слот, записываем клиента
    slots.remove(slot)
    set_expert_slots(spec['Telegram ID'], slots)
    # Уведомляем клиента и эксперта
    await q.message.reply_text(f"Вы записались к специалисту: {spec['ФИО']} на {slot}")
    # Уведомление эксперту
    if spec.get('Telegram ID'):
        try:
            await q.bot.send_message(
                chat_id=spec['Telegram ID'],
                text=f"У вас новая запись: {update.effective_user.full_name} (@{update.effective_user.username}) на {slot}"
            )
        except Exception as e:
            pass
    return ConversationHandler.END

# --- Application and Handlers
application = ApplicationBuilder().token(TOKEN).build()

conv_reg = ConversationHandler(
    entry_points=[CommandHandler("register", reg_start)],
    states={
        REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
        REG_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)],
        REG_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field)],
        REG_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_PHOTO: [MessageHandler(filters.PHOTO, reg_photo)],
        REG_PHOTO2: [MessageHandler(filters.PHOTO, reg_photo2)]
    },
    fallbacks=[CommandHandler("cancel", reg_cancel)]
)

conv_addslot = ConversationHandler(
    entry_points=[CommandHandler("addslot", addslot_start)],
    states={
        ADDSLOT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addslot_date)],
        ADDSLOT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addslot_time)],
    },
    fallbacks=[]
)

conv_main = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CHOOSING_REGION: [CallbackQueryHandler(cb_region)],
        CHOOSING_FIELD: [
            CallbackQueryHandler(cb_field),
            CallbackQueryHandler(cb_back_region, pattern='^back_region$')
        ],
        CHOOSING_SPEC: [
            CallbackQueryHandler(cb_spec, pattern='^spec_'),
            CallbackQueryHandler(cb_back_field, pattern='^back_field$')
        ],
        CHOOSING_DATE: [
            CallbackQueryHandler(cb_date, pattern='^date_'),
            CallbackQueryHandler(cb_back_spec, pattern='^back_spec$')
        ],
        CHOOSING_TIME: [
            CallbackQueryHandler(cb_time, pattern='^time_'),
            CallbackQueryHandler(cb_back_date, pattern='^back_date$')
        ]
    },
    fallbacks=[]
)

application.add_handler(conv_reg)
application.add_handler(conv_addslot)
application.add_handler(conv_main)

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
