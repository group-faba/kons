import os
import json
import threading
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ConversationHandler, ContextTypes
)
from telegram_bot_calendar import DetailedTelegramCalendar, LSTEP

logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
ADMIN_ID   = int(os.environ.get('ADMIN_CHAT_ID', '0'))
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT       = int(os.environ.get('PORT', '8080'))

app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc    = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID).sheet1

CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC, CHOICE_DATE, CHOICE_TIME = range(5)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    rows = sheet.get_all_records()
    seen = set()
    buttons = []
    for r in rows:
        if r['Регион'] not in seen:
            seen.add(r['Регион'])
            buttons.append([InlineKeyboardButton(r['Регион'], callback_data=r['Регион'])])
    await update.message.reply_text('Выберите регион:', reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data['region'] = update.callback_query.data
    rows = sheet.get_all_records()
    seen = set()
    buttons = []
    for r in rows:
        if r['Регион'] == ctx.user_data['region'] and r['Сфера'] not in seen:
            seen.add(r['Сфера'])
            buttons.append([InlineKeyboardButton(r['Сфера'], callback_data=r['Сфера'])])
    await update.callback_query.edit_message_text(f'Регион: {ctx.user_data["region"]}\nВыберите сферу:', reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_INDUSTRY

async def cb_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data['industry'] = update.callback_query.data
    rows = sheet.get_all_records()
    specs = [r for r in rows if r['Регион']==ctx.user_data['region'] and r['Сфера']==ctx.user_data['industry']]
    ctx.user_data['specs'] = specs
    buttons = [[InlineKeyboardButton(s['ФИО'], callback_data=str(i))] for i, s in enumerate(specs)]
    await update.callback_query.edit_message_text(f'Сфера: {ctx.user_data["industry"]}\nВыберите консультанта:', reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    idx = int(update.callback_query.data)
    ctx.user_data['spec_idx'] = idx
    ctx.user_data['selected_spec'] = ctx.user_data['specs'][idx]
    calendar, step = DetailedTelegramCalendar().build()
    await update.callback_query.edit_message_text(f"Выберите {LSTEP[step]}:", reply_markup=calendar)
    return CHOICE_DATE

async def cb_calendar(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    calendar = DetailedTelegramCalendar()
    result, key, step = calendar.process(query.data)
    if not result and key:
        await query.edit_message_text(f"Выберите {LSTEP[step]}:", reply_markup=key)
        return CHOICE_DATE
    elif result:
        ctx.user_data['selected_date'] = result.strftime('%d.%m.%Y')
        # Найти все свободные времена у специалиста на эту дату
        rows = sheet.get_all_records()
        spec = ctx.user_data['selected_spec']
        times = []
        for r in rows:
            if r['ФИО']==spec['ФИО'] and r.get('Дата','')==ctx.user_data['selected_date'] and r.get('Статус','')=='свободно':
                times.append(r['Время'])
        if not times:
            await query.edit_message_text("На эту дату свободного времени нет. Выберите другую дату.")
            calendar, step = DetailedTelegramCalendar().build()
            await query.message.reply_text(f"Выберите {LSTEP[step]}:", reply_markup=calendar)
            return CHOICE_DATE
        buttons = [[InlineKeyboardButton(t, callback_data=t)] for t in times]
        await query.edit_message_text(f"Свободное время на {ctx.user_data['selected_date']}:", reply_markup=InlineKeyboardMarkup(buttons))
        return CHOICE_TIME

async def cb_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    time = update.callback_query.data
    ctx.user_data['selected_time'] = time
    spec = ctx.user_data['selected_spec']
    await update.callback_query.edit_message_text(
        f"Вы записались к {spec['ФИО']} на {ctx.user_data['selected_date']} в {time}."
    )
    # Сохраняем запись в Google таблицу — находим строку и меняем статус на 'занято'
    rows = sheet.get_all_records()
    cell = None
    for idx, r in enumerate(rows):
        if (r['ФИО']==spec['ФИО'] and r.get('Дата','')==ctx.user_data['selected_date'] and r.get('Время','')==time):
            cell = idx+2  # первая строка — шапка
            break
    if cell:
        sheet.update_cell(cell, list(rows[0].keys()).index('Статус')+1, 'занято')
    user = update.effective_user
    await ctx.bot.send_message(ADMIN_ID, f"Запись: {user.full_name} (id={user.id}) -> {spec['ФИО']} [{ctx.user_data['selected_date']} {time}]")
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Отменено.')
    return ConversationHandler.END

application = ApplicationBuilder().token(TOKEN).build()
conv = ConversationHandler(
    entry_points=[CommandHandler('start', cmd_start)],
    states={
        CHOICE_REGION:   [CallbackQueryHandler(cb_region)],
        CHOICE_INDUSTRY: [CallbackQueryHandler(cb_industry)],
        CHOICE_SPEC:     [CallbackQueryHandler(cb_spec)],
        CHOICE_DATE:     [CallbackQueryHandler(cb_calendar)],
        CHOICE_TIME:     [CallbackQueryHandler(cb_time)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
application.add_handler(conv)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
