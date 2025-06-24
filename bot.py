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
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes
)

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
    """Собирает все анкеты специалистов из вкладок (кроме 'Лист1')"""
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

# --- Flask healthcheck
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

# --- Conversation этапы
CHOOSE_REGION, CHOOSE_FIELD, CHOOSE_SPEC, SHOW_SPEC = range(4)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    specs = get_specialists()
    if not specs:
        await update.message.reply_text("Нет специалистов.")
        return ConversationHandler.END
    # Уникальные регионы
    regions = sorted(set(spec['Город'] for spec in specs))
    kb = [[InlineKeyboardButton(r, callback_data=r)] for r in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSE_REGION

async def region_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data
    ctx.user_data['region'] = region
    specs = get_specialists()
    # Уникальные сферы в выбранном регионе
    fields = sorted(set(spec['Сфера'] for spec in specs if spec['Город'] == region))
    kb = [[InlineKeyboardButton(f, callback_data=f)] for f in fields]
    await update.callback_query.edit_message_text(f"Регион: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSE_FIELD

async def field_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = update.callback_query.data
    ctx.user_data['field'] = field
    specs = get_specialists()
    # ФИО всех специалистов по региону/сфере
    filtered = [s for s in specs if s['Город'] == ctx.user_data['region'] and s['Сфера'] == field]
    ctx.user_data['filtered'] = filtered
    kb = [
        [InlineKeyboardButton(spec['ФИО'], callback_data=spec['sheet_name'])]
        for spec in filtered
    ]
    await update.callback_query.edit_message_text(f"Сфера: {field}\nВыберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSE_SPEC

async def spec_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    sheet_name = update.callback_query.data
    ws = spreadsheet.worksheet(sheet_name)
    row = ws.get_all_records()[0]
    ctx.user_data['selected'] = row
    kb = [
        [
            InlineKeyboardButton("Назад", callback_data='back'),
            InlineKeyboardButton("Выбрать этого специалиста", callback_data='choose')
        ]
    ]
    caption = f"{row['ФИО']}\n\n{row.get('Описание', '')}"
    if row.get('photo_file_id'):
        await update.callback_query.message.reply_photo(photo=row['photo_file_id'], caption=caption, reply_markup=InlineKeyboardMarkup(kb))
        try:
            await update.callback_query.delete_message()
        except Exception:
            pass
    else:
        await update.callback_query.edit_message_text(caption, reply_markup=InlineKeyboardMarkup(kb))
    return SHOW_SPEC

async def addslot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Узнать кто ты — через id, найти свою вкладку
    username = update.effective_user.username or ''
    user_id = update.effective_user.id
    # Поиск вкладки
    ws = None
    for sheet in spreadsheet.worksheets():
        records = sheet.get_all_records()
        if records and str(records[0].get("Telegram ID")) == str(user_id):
            ws = sheet
            break
    if not ws:
        await update.message.reply_text("Ваша анкета не найдена.")
        return

    await update.message.reply_text("Напиши дату и время слота (например: 25.06 15:00)")
    ctx.user_data['addslot_sheet'] = ws.title
    return 1  # Переход к ожиданию текста

async def addslot_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    slot = update.message.text.strip()
    ws = spreadsheet.worksheet(ctx.user_data['addslot_sheet'])
    # Можно хранить отдельный столбец "Слоты" или строкой, или отдельными строками
    ws.append_row(['', '', '', '', '', '', '', slot])  # или другой способ — зависит от структуры
    await update.message.reply_text("Слот добавлен.")
    return ConversationHandler.END

async def back_from_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    filtered = ctx.user_data.get('filtered', [])
    kb = [
        [InlineKeyboardButton(spec['ФИО'], callback_data=spec['sheet_name'])]
        for spec in filtered
    ]
    await update.callback_query.message.reply_text(
        f"Сфера: {ctx.user_data.get('field')}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    try:
        await update.callback_query.delete_message()
    except Exception:
        pass
    return CHOOSE_SPEC

async def choose_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    selected = ctx.user_data.get('selected')
    await update.callback_query.message.reply_text(f"Вы выбрали: {selected['ФИО']}\nВаша заявка отправлена!")
    # Здесь отправляй админу, либо специалисту, либо делай запись
    try:
        await update.callback_query.delete_message()
    except Exception:
        pass
    return ConversationHandler.END

application = ApplicationBuilder().token(TOKEN).build()

conv_main = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CHOOSE_REGION: [CallbackQueryHandler(region_chosen)],
        CHOOSE_FIELD:  [CallbackQueryHandler(field_chosen)],
        CHOOSE_SPEC:   [CallbackQueryHandler(spec_chosen, pattern="^(?!back$|choose$).+")],
        SHOW_SPEC: [
            CallbackQueryHandler(back_from_spec, pattern="^back$"),
            CallbackQueryHandler(choose_spec, pattern="^choose$"),
        ],
    },
    fallbacks=[],
)

application.add_handler(conv_main)

application.add_handler(CommandHandler("addslot", addslot_start))

addslot_conv = ConversationHandler(
    entry_points=[CommandHandler("addslot", addslot_start)],
    states={
        1: [MessageHandler(filters.TEXT & ~filters.COMMAND, addslot_receive)],
    },
    fallbacks=[],
)
application.add_handler(addslot_conv)

# --- Flask запуск
if __name__ == "__main__":
    import threading
    threading.Thread(target=app.run, kwargs={"host":"0.0.0.0", "port":PORT}, daemon=True).start()
    application.run_polling()
