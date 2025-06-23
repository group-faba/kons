import os
import json
import threading
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes
)

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

CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC, CHOICE_DATE, CHOICE_TIME, CONFIRM = range(6)

def get_rows():
    return sheet.get_all_records()

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    rows = get_rows()
    seen = set(); buttons = []
    for r in rows:
        if r['Регион'] not in seen:
            seen.add(r['Регион'])
            buttons.append([InlineKeyboardButton(r['Регион'], callback_data=r['Регион'])])
    await update.message.reply_text('Выберите регион:', reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data.clear()
    region = update.callback_query.data
    ctx.user_data['region'] = region
    rows = get_rows()
    seen = set(); buttons = []
    for r in rows:
        if r['Регион'] == region and r['Сфера'] not in seen:
            seen.add(r['Сфера'])
            buttons.append([InlineKeyboardButton(r['Сфера'], callback_data=r['Сфера'])])
    await update.callback_query.edit_message_text(f'Регион: {region}\nВыберите сферу:', reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_INDUSTRY

async def cb_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    industry = update.callback_query.data
    ctx.user_data['industry'] = industry
    rows = get_rows()
    # собираем специалистов по региону/сфере
    specs = []
    seen = set()
    for r in rows:
        if r['Регион']==ctx.user_data['region'] and r['Сфера']==industry:
            key = r['ФИО']
            if key not in seen:
                seen.add(key)
                specs.append(r)
    ctx.user_data['specs'] = specs
    buttons = [[InlineKeyboardButton(s['ФИО'], callback_data=str(i))] for i, s in enumerate(specs)]
    await update.callback_query.edit_message_text(f'Сфера: {industry}\nВыберите специалиста:', reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    idx = int(update.callback_query.data)
    spec = ctx.user_data['specs'][idx]
    ctx.user_data['fio'] = spec['ФИО']
    ctx.user_data['spec_info'] = spec
    rows = get_rows()
    # собираем уникальные даты для выбранного специалиста
    dates = sorted({r['Дата'] for r in rows if r['ФИО'] == spec['ФИО'] and r['Регион'] == ctx.user_data['region'] and r['Сфера'] == ctx.user_data['industry'] and r['Дата']})
    if not dates:
        await update.callback_query.edit_message_text(f"Нет доступных дат для {spec['ФИО']}")
        return ConversationHandler.END
    ctx.user_data['dates'] = dates
    buttons = [[InlineKeyboardButton(date, callback_data=date)] for date in dates]
    # показываем описание и сертификат
    desc = spec.get('Описание', '')
    cert = spec.get('Сертификат', '')
    msg = f"Специалист: {spec['ФИО']}\n\n{desc}\n\nВыберите дату консультации:"
    if cert:
        await ctx.bot.send_photo(chat_id=update.effective_chat.id, photo=cert, caption=msg)
        await update.callback_query.delete_message()  # убираем старую карточку
    else:
        await update.callback_query.edit_message_text(msg)
    await ctx.bot.send_message(chat_id=update.effective_chat.id, text="Выберите дату:", reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_DATE

async def cb_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    date = update.callback_query.data
    ctx.user_data['date'] = date
    rows = get_rows()
    times = sorted({r['Время'] for r in rows if r['ФИО']==ctx.user_data['fio'] and r['Дата']==date and r['Время']})
    if not times:
        await update.callback_query.edit_message_text("Нет свободного времени на эту дату.")
        return ConversationHandler.END
    buttons = [[InlineKeyboardButton(time, callback_data=time)] for time in times]
    await update.callback_query.edit_message_text(f"Выбрана дата: {date}\nВыберите время:", reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_TIME

async def cb_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    time = update.callback_query.data
    ctx.user_data['time'] = time
    info = ctx.user_data['spec_info']
    text = f"""Запись на консультацию:
<b>ФИО:</b> {info['ФИО']}
<b>Регион:</b> {info['Регион']}
<b>Сфера:</b> {info['Сфера']}
<b>Дата:</b> {ctx.user_data['date']}
<b>Время:</b> {ctx.user_data['time']}
"""
    buttons = [
        [InlineKeyboardButton("Назад", callback_data='back')],
        [InlineKeyboardButton("Подтвердить", callback_data='confirm')]
    ]
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')
    return CONFIRM

async def cb_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    data = ctx.user_data
    if update.callback_query.data == 'confirm':
        # отправляем админу, здесь можно записать в гугл-таблицу
        await ctx.bot.send_message(
            ADMIN_ID,
            f"Новая запись: {update.effective_user.full_name} (id={update.effective_user.id})\n"
            f"На {data['fio']} ({data['date']} {data['time']})"
        )
        await update.callback_query.edit_message_text("Ваша запись подтверждена!")
        # тут можно добавить удаление этого времени из таблицы (если надо)
        return ConversationHandler.END
    elif update.callback_query.data == 'back':
        # вернуться к выбору времени
        rows = get_rows()
        times = sorted({r['Время'] for r in rows if r['ФИО']==data['fio'] and r['Дата']==data['date'] and r['Время']})
        buttons = [[InlineKeyboardButton(time, callback_data=time)] for time in times]
        await update.callback_query.edit_message_text(f"Выбрана дата: {data['date']}\nВыберите время:", reply_markup=InlineKeyboardMarkup(buttons))
        return CHOICE_TIME

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
        CHOICE_DATE:     [CallbackQueryHandler(cb_date)],
        CHOICE_TIME:     [CallbackQueryHandler(cb_time)],
        CONFIRM:         [CallbackQueryHandler(cb_confirm)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
application.add_handler(conv)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
