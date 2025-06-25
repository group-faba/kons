import os
import json
import logging
from datetime import datetime, timedelta
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

logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT       = int(os.environ.get('PORT', '8080'))

SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SHEET_ID)

app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# =====================
# Анкета специалиста
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
    file_id = ''
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    ctx.user_data['photo_file_id'] = file_id
    fio = ctx.user_data['fio']
    tab_name = f"{fio}_{update.effective_user.id}"
    try:
        ws = spreadsheet.add_worksheet(tab_name, rows="50", cols="10")
    except Exception:
        ws = spreadsheet.worksheet(tab_name)
    ws.append_row(["ФИО", "Город", "Сфера", "Описание", "photo_file_id", "Telegram ID", "Username"])
    ws.append_row([
        fio,
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        ctx.user_data['photo_file_id'],
        update.effective_user.id,
        update.effective_user.username or ''
    ])
    await update.message.reply_text("Спасибо, вы зарегистрированы как специалист!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отмена регистрации.")
    return ConversationHandler.END

def get_specialists():
    specs = []
    for ws in spreadsheet.worksheets():
        if ws.title == 'Лист1':
            continue
        data = ws.get_all_records()
        if data:
            card = data[0]
            card['sheet_name'] = ws.title
            specs.append(card)
    return specs

# =====================
# Логика выбора специалиста

CHOOSING_REGION, CHOOSING_FIELD, CHOOSING_SPEC, SHOW_SPEC = range(4)

def get_main_records():
    ws = spreadsheet.worksheet('Лист1')
    return ws.get_all_records()

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    records = get_main_records()
    regions = sorted(set(r['Город'] for r in records if r['Город']))
    kb = [[InlineKeyboardButton(region, callback_data=f"region|{region}")]
          for region in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data.split('|')[1]
    records = get_main_records()
    fields = sorted(set(r['Сфера'] for r in records if r['Город'] == region and r['Сфера']))
    kb = [[InlineKeyboardButton(field, callback_data=f"field|{region}|{field}")]
          for field in fields]
    await update.callback_query.edit_message_text(f"Регион: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, region, field = update.callback_query.data.split('|')
    records = get_main_records()
    specs = [r for r in records if r['Город'] == region and r['Сфера'] == field]
    kb = [
        [InlineKeyboardButton(r['ФИО'], callback_data=f"spec|{r['ФИО']}|{region}|{field}")]
        for r in specs
    ]
    await update.callback_query.edit_message_text(
        f"Сфера: {field}\nВыберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, fio, region, field = update.callback_query.data.split('|')
    ws = spreadsheet.worksheet('Лист1')
    specs = [r for r in ws.get_all_records() if r['ФИО'] == fio and r['Город'] == region and r['Сфера'] == field]
    if not specs:
        await update.callback_query.edit_message_text("Данные не найдены.")
        return ConversationHandler.END
    spec = specs[0]
    kb = [
        [InlineKeyboardButton("Назад", callback_data=f"back|{region}|{field}")],
        [InlineKeyboardButton("Выбрать этого специалиста", callback_data=f"choose|{fio}|{region}|{field}")]
    ]
    text = f"{spec['ФИО']}\n{spec['Описание']}"
    if spec.get('photo_file_id'):
        await update.callback_query.message.reply_photo(
            photo=spec['photo_file_id'],
            caption=text,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        await update.callback_query.delete_message()
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return SHOW_SPEC

async def cb_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, region, field = update.callback_query.data.split('|')
    records = get_main_records()
    specs = [r for r in records if r['Город'] == region and r['Сфера'] == field]
    kb = [
        [InlineKeyboardButton(r['ФИО'], callback_data=f"spec|{r['ФИО']}|{region}|{field}")]
        for r in specs
    ]
    await update.callback_query.message.reply_text(
        f"Сфера: {field}\nВыберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))
    await update.callback_query.delete_message()
    return CHOOSING_SPEC

async def cb_choose(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, fio, region, field = update.callback_query.data.split('|')
    await update.callback_query.edit_message_text(
        f"Вы записались к специалисту: {fio}"
    )
    # Тут можно отправить уведомление специалисту или еще что-то сделать
    return ConversationHandler.END

# =====================
# Слоты расписания

async def addslot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or ''
    fio = ''
    ws = None

    # Поиск вкладки специалиста по Telegram ID
    for w in spreadsheet.worksheets():
        rows = w.get_all_records()
        if rows and str(rows[0].get("Telegram ID")) == user_id:
            ws = w
            fio = rows[0].get("ФИО", "")
            break

    if not ws:
        await update.message.reply_text("Вы не зарегистрированы как специалист.")
        return

    # Проверка и добавление слотов на сегодня и завтра
    slots = []
    today = datetime.now()
    for days_ahead in [0, 1]:
        date_str = (today + timedelta(days=days_ahead)).strftime('%d.%m.%Y')
        for hour in range(10, 18):  # 10:00 - 17:00
            time_str = f"{hour:02d}:00"
            slots.append((date_str, time_str, "Свободно"))

    # Проверить, есть ли заголовки "Дата", "Время", "Статус", если нет — добавить
    headers = ws.row_values(1)
    if "Дата" not in headers:
        ws.append_row(["Дата", "Время", "Статус"])

    # Получить все уже существующие записи (даты+время)
    all_vals = ws.get_all_records()
    existing = set((r.get("Дата"), r.get("Время")) for r in all_vals if r.get("Дата") and r.get("Время"))
    new_count = 0

    for date_str, time_str, status in slots:
        if (date_str, time_str) not in existing:
            ws.append_row([date_str, time_str, status])
            new_count += 1

    await update.message.reply_text(f"Добавлено слотов: {new_count}")

# =====================
# Handlers
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

conv_main = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CHOOSING_REGION: [CallbackQueryHandler(cb_region, pattern='^region\|')],
        CHOOSING_FIELD:  [CallbackQueryHandler(cb_field, pattern='^field\|')],
        CHOOSING_SPEC:   [CallbackQueryHandler(cb_spec, pattern='^spec\|')],
        SHOW_SPEC: [
            CallbackQueryHandler(cb_back, pattern='^back\|'),
            CallbackQueryHandler(cb_choose, pattern='^choose\|'),
        ],
    },
    fallbacks=[],
)

application.add_handler(conv_reg)
application.add_handler(conv_main)
application.add_handler(CommandHandler("addslot", addslot))

# --- Запуск
if __name__ == "__main__":
    import threading
    threading.Thread(target=app.run, kwargs={'host':'0.0.0.0', 'port':PORT}, daemon=True).start()
    application.run_polling()
