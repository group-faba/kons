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

def get_specialists():
    ws = spreadsheet.worksheet('Лист1')
    records = ws.get_all_records()
    return records

def update_schedule(fio, time_slot, user_name, user_id):
    ws = spreadsheet.worksheet('Лист1')
    records = ws.get_all_records()
    for idx, row in enumerate(records, start=2):
        if row['ФИО'] == fio:
            # Обновляем расписание
            raw_schedule = row.get('Расписание', '')
            slots = [s for s in raw_schedule.split(';') if s]
            new_slots = []
            for s in slots:
                if s.startswith(time_slot) and s.endswith('свободно'):
                    new_slots.append(f"{time_slot}:занято")
                else:
                    new_slots.append(s)
            ws.update_cell(idx, ws.find('Расписание').col, ';'.join(new_slots))
            # Запись клиента (в столбце J, если есть)
            try:
                col_j = ws.find('Username').col + 1
            except:
                col_j = ws.col_count + 1
            ws.update_cell(idx, col_j, f"{user_name} ({user_id}) записался на {time_slot}")
            return row['Telegram ID']
    return None

def get_free_slots(row):
    slots = []
    raw_schedule = row.get('Расписание', '')
    for s in raw_schedule.split(';'):
        if s.strip().endswith('свободно'):
            slots.append(s.strip().split(':')[0])
    return slots

CHOOSING_REGION, CHOOSING_FIELD, CHOOSING_SPEC, CHOOSING_SLOT = range(4)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    records = get_specialists()
    regions = sorted(set(r['Город'] for r in records if r['Город']))
    kb = [[InlineKeyboardButton(region, callback_data=f"region_{region}")] for region in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data.replace('region_', '')
    ctx.user_data['region'] = region
    records = get_specialists()
    fields = sorted(set(r['Сфера'] for r in records if r['Город'] == region))
    kb = [[InlineKeyboardButton(f, callback_data=f"field_{f}")] for f in fields]
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nВыберите сферу:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = update.callback_query.data.replace('field_', '')
    ctx.user_data['field'] = field
    records = get_specialists()
    specs = [r for r in records if r['Город'] == ctx.user_data['region'] and r['Сфера'] == field]
    kb = [[InlineKeyboardButton(s['ФИО'], callback_data=f"spec_{s['ФИО']}")] for s in specs]
    await update.callback_query.edit_message_text(
        f"Сфера: {field}\nВыберите специалиста:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    fio = update.callback_query.data.replace('spec_', '')
    ctx.user_data['fio'] = fio
    records = get_specialists()
    spec = next(r for r in records if r['ФИО'] == fio)
    slots = get_free_slots(spec)
    if not slots:
        await update.callback_query.edit_message_text("Нет свободного времени для записи.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(slot, callback_data=f"slot_{slot}")] for slot in slots]
    await update.callback_query.edit_message_text(
        f"{fio}\n{spec['Описание']}\nВыберите время для записи:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSING_SLOT

async def cb_slot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    slot = update.callback_query.data.replace('slot_', '')
    ctx.user_data['slot'] = slot
    fio = ctx.user_data['fio']
    user_name = update.effective_user.full_name
    user_id = update.effective_user.id
    expert_id = update_schedule(fio, slot, user_name, user_id)
    await update.callback_query.edit_message_text(
        f"Вы записались к специалисту: {fio} на {slot}"
    )
    if expert_id:
        try:
            await ctx.bot.send_message(
                chat_id=int(expert_id),
                text=f"Запись на консультацию: {user_name} ({user_id}) выбрал время {slot}"
            )
        except:
            pass
    return ConversationHandler.END

application = ApplicationBuilder().token(TOKEN).build()
conv = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        CHOOSING_REGION: [CallbackQueryHandler(cb_region, pattern="^region_")],
        CHOOSING_FIELD:  [CallbackQueryHandler(cb_field, pattern="^field_")],
        CHOOSING_SPEC:   [CallbackQueryHandler(cb_spec, pattern="^spec_")],
        CHOOSING_SLOT:   [CallbackQueryHandler(cb_slot, pattern="^slot_")],
    },
    fallbacks=[],
)
application.add_handler(conv)

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
