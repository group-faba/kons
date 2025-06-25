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
ws = spreadsheet.sheet1

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
    telegram_id = str(update.effective_user.id)
    users = ws.get_all_records()
    for user in users:
        if str(user.get('Telegram ID')) == telegram_id:
            await update.message.reply_text("Вы уже зарегистрированы как специалист.")
            return ConversationHandler.END

    file_id = ''
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    row = [
        ctx.user_data.get('fio',''),
        ctx.user_data.get('city',''),
        ctx.user_data.get('field',''),
        ctx.user_data.get('desc',''),
        file_id,
        telegram_id,
        update.effective_user.username or '',
        ""
    ]
    ws.append_row(row)
    await update.message.reply_text("Спасибо, вы зарегистрированы как специалист!\nЧтобы добавить расписание, используйте команду /addslot")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отмена регистрации.")
    return ConversationHandler.END

# --- Conversation для /addslot
SLOT_DATE, SLOT_TIME, SLOT_CONFIRM = range(3)
def _find_expert_row(telegram_id):
    rows = ws.get_all_values()
    for idx, row in enumerate(rows):
        if len(row) > 5 and str(row[5]) == str(telegram_id):
            return idx + 1  # +1 для корректной индексации в gspread
    return None

def _get_slot_keyboard(selected_times=None):
    # часы с 10:00 до 18:00
    times = [f"{h:02d}:00" for h in range(10, 19)]
    kb = []
    for t in times:
        label = f"✅ {t}" if selected_times and t in selected_times else t
        kb.append([InlineKeyboardButton(label, callback_data=f"slot_{t}")])
    kb.append([InlineKeyboardButton("Подтвердить", callback_data="confirm_slots")])
    return InlineKeyboardMarkup(kb)

async def addslot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    telegram_id = str(update.effective_user.id)
    idx = _find_expert_row(telegram_id)
    if not idx:
        await update.message.reply_text("Ваша анкета не найдена. Зарегистрируйтесь через /register.")
        return ConversationHandler.END

    today = datetime.today().date()
    tomorrow = today + timedelta(days=1)
    kb = [
        [InlineKeyboardButton(today.strftime("%d.%m.%Y"), callback_data=f"date_{today}")],
        [InlineKeyboardButton(tomorrow.strftime("%d.%m.%Y"), callback_data=f"date_{tomorrow}")]
    ]
    ctx.user_data['selected_date'] = None
    ctx.user_data['selected_times'] = []
    await update.message.reply_text("Выберите дату для записи:", reply_markup=InlineKeyboardMarkup(kb))
    return SLOT_DATE

async def addslot_pick_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    date = data.split("_", 1)[1]
    ctx.user_data['selected_date'] = date
    ctx.user_data['selected_times'] = []
    await update.callback_query.edit_message_text(
        f"Дата: {datetime.strptime(date, '%Y-%m-%d').strftime('%d.%m.%Y')}\nВыберите время (можно несколько):",
        reply_markup=_get_slot_keyboard()
    )
    return SLOT_TIME

async def addslot_pick_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    if data.startswith("slot_"):
        t = data[5:]
        if t in ctx.user_data['selected_times']:
            ctx.user_data['selected_times'].remove(t)
        else:
            ctx.user_data['selected_times'].append(t)
        # обновить клаву
        await update.callback_query.edit_message_text(
            f"Дата: {datetime.strptime(ctx.user_data['selected_date'], '%Y-%m-%d').strftime('%d.%m.%Y')}\nВыберите время (можно несколько):",
            reply_markup=_get_slot_keyboard(ctx.user_data['selected_times'])
        )
        return SLOT_TIME
    elif data == "confirm_slots":
        if not ctx.user_data['selected_times']:
            await update.callback_query.answer("Сначала выберите хотя бы одно время.", show_alert=True)
            return SLOT_TIME
        # сохранить в таблицу
        telegram_id = str(update.effective_user.id)
        idx = _find_expert_row(telegram_id)
        if not idx:
            await update.callback_query.edit_message_text("Ваша анкета не найдена.")
            return ConversationHandler.END
        row = ws.row_values(idx)
        slot_col = 8  # H = 8
        old = row[slot_col - 1] if len(row) >= slot_col else ''
        new_slots = [f"{ctx.user_data['selected_date']} {t}" for t in ctx.user_data['selected_times']]
        updated = old + ';' + ';'.join(new_slots) if old else ';'.join(new_slots)
        ws.update_cell(idx, slot_col, updated)
        await update.callback_query.edit_message_text("Время для записи успешно добавлено!")
        return ConversationHandler.END

# --- Запись на консультацию
CHOOSING_REGION, CHOOSING_FIELD, CHOOSING_SPEC, CHOOSING_SLOT = range(4)

def _get_regions():
    users = ws.get_all_records()
    return sorted(set(u['Город'] for u in users if u.get('Город')))

def _get_fields(region):
    users = ws.get_all_records()
    return sorted(set(u['Сфера'] for u in users if u.get('Город') == region and u.get('Сфера')))

def _get_specs(region, field):
    users = ws.get_all_records()
    return [u for u in users if u.get('Город') == region and u.get('Сфера') == field]

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    regions = _get_regions()
    if not regions:
        await update.message.reply_text("Нет зарегистрированных специалистов.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(r, callback_data=f"region_{r}")] for r in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data.split("_", 1)[1]
    ctx.user_data['region'] = region
    fields = _get_fields(region)
    if not fields:
        await update.callback_query.edit_message_text("Нет специалистов по выбранному региону.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f, callback_data=f"field_{f}")] for f in fields]
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nВыберите сферу:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = update.callback_query.data.split("_", 1)[1]
    ctx.user_data['field'] = field
    specs = _get_specs(ctx.user_data['region'], field)
    if not specs:
        await update.callback_query.edit_message_text("Нет специалистов по выбранной сфере.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(s['ФИО'], callback_data=f"spec_{s['Telegram ID']}")] for s in specs]
    await update.callback_query.edit_message_text(
        f"Регион: {ctx.user_data['region']}\nСфера: {field}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    ctx.user_data['specs'] = {s['Telegram ID']: s for s in specs}
    return CHOOSING_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    spec_id = update.callback_query.data.split("_", 1)[1]
    spec = ctx.user_data['specs'][spec_id]
    desc = spec.get('Описание', '')
    fio = spec['ФИО']
    photo = spec.get('photo_file_id', '')
    slot_str = spec.get('', '') or spec.get('','')
    row = None
    users = ws.get_all_records()
    for u in users:
        if str(u.get('Telegram ID')) == spec_id:
            row = u
            break
    slots = []
    if row and len(row) > 7 and row.get(''):
        slots = [s for s in row[''].split(';') if s]
    # оставить только будущие
    now = datetime.now()
    avail = []
    for s in slots:
        try:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
        except Exception:
            continue
        if dt > now:
            avail.append(s)
    if not avail:
        await update.callback_query.edit_message_text(
            f"{fio}\n{desc}\nНет свободного времени для записи."
        )
        return ConversationHandler.END
    kb = [
        [InlineKeyboardButton(datetime.strptime(s, "%Y-%m-%d %H:%M").strftime("%d.%m.%Y %H:%M"), callback_data=f"slot_{spec_id}_{s}")]
        for s in avail
    ]
    text = f"{fio}\n{desc}"
    if photo:
        await update.callback_query.message.reply_photo(
            photo=photo,
            caption=text,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        await update.callback_query.delete_message()
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['choosing_spec'] = spec
    return CHOOSING_SLOT

async def cb_slot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data.split("_", 2)
    spec_id = data[1]
    slot = data[2]
    users = ws.get_all_records()
    idx = None
    for i, u in enumerate(users, start=2):
        if str(u.get('Telegram ID')) == spec_id:
            idx = i
            break
    if not idx:
        await update.callback_query.edit_message_text("Ошибка, специалист не найден.")
        return ConversationHandler.END
    row = ws.row_values(idx)
    slot_col = 8
    slots = row[slot_col-1] if len(row) >= slot_col else ''
    slots_list = [s for s in slots.split(';') if s]
    if slot not in slots_list:
        await update.callback_query.edit_message_text("Увы, время уже занято.")
        return ConversationHandler.END
    slots_list.remove(slot)
    ws.update_cell(idx, slot_col, ';'.join(slots_list))
    await update.callback_query.edit_message_text(
        f"Вы записались к специалисту: {row[0]} на {datetime.strptime(slot, '%Y-%m-%d %H:%M').strftime('%d.%m.%Y %H:%M')}"
    )
    # Уведомление эксперту
    try:
        spec_tid = int(row[5])
        username = update.effective_user.username or ''
        fio = update.effective_user.full_name
        text = f"К вам записался {fio} (@{username}) на {datetime.strptime(slot, '%Y-%m-%d %H:%M').strftime('%d.%m.%Y %H:%M')}"
        await ctx.bot.send_message(chat_id=spec_tid, text=text)
    except Exception as e:
        logging.warning(f"Ошибка отправки уведомления эксперту: {e}")
    return ConversationHandler.END

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

conv_addslot = ConversationHandler(
    entry_points=[CommandHandler("addslot", addslot_start)],
    states={
        SLOT_DATE:   [CallbackQueryHandler(addslot_pick_date, pattern="^date_")],
        SLOT_TIME:   [CallbackQueryHandler(addslot_pick_time)],
    },
    fallbacks=[CommandHandler("cancel", reg_cancel)],
)

conv_main = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CHOOSING_REGION: [CallbackQueryHandler(cb_region, pattern="^region_")],
        CHOOSING_FIELD:  [CallbackQueryHandler(cb_field, pattern="^field_")],
        CHOOSING_SPEC:   [CallbackQueryHandler(cb_spec, pattern="^spec_")],
        CHOOSING_SLOT:   [CallbackQueryHandler(cb_slot, pattern="^slot_")],
    },
    fallbacks=[],
)

application.add_handler(conv_reg)
application.add_handler(conv_addslot)
application.add_handler(conv_main)

# --- Render-ready запуск ---
def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
