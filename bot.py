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
    CallbackQueryHandler, ConversationHandler,
    ContextTypes
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

CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC, CHOICE_SLOT = range(4)

def get_rows():
    return sheet.get_all_records()

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    rows = get_rows()
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
    region = update.callback_query.data
    ctx.user_data['region'] = region
    rows = get_rows()
    seen = set()
    buttons = []
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
    specs = []
    seen = set()
    for r in rows:
        if r['Регион'] == ctx.user_data['region'] and r['Сфера'] == industry and r['ФИО'] not in seen:
            seen.add(r['ФИО'])
            specs.append(r)
    ctx.user_data['specs'] = specs
    buttons = [[InlineKeyboardButton(s['ФИО'], callback_data=str(i))] for i, s in enumerate(specs)]
    await update.callback_query.edit_message_text(f'Сфера: {industry}\nВыберите консультанта:', reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    idx = int(update.callback_query.data)
    spec = ctx.user_data['specs'][idx]
    ctx.user_data['selected_spec'] = spec
    # Слоты для выбранного специалиста
    rows = get_rows()
    slots = []
    for r in rows:
        if r['ФИО'] == spec['ФИО'] and r['Регион'] == spec['Регион'] and r['Сфера'] == spec['Сфера']:
            slots.append((r['Дата'], r['Время']))
    ctx.user_data['slots'] = slots
    if not slots:
        await update.callback_query.edit_message_text(f"{spec['ФИО']} (нет доступных слотов)")
        return ConversationHandler.END
    buttons = [[InlineKeyboardButton(f"{date} {time}", callback_data=f"{date}|{time}")]
               for date, time in slots]
    # описание и картинка
    text = f"{spec['ФИО']}\n{spec.get('Описание','')}\nВыберите дату и время:"
    photo = spec.get('Сертификат')
    if photo:
        await ctx.bot.send_photo(chat_id=update.effective_chat.id, photo=photo, caption=text,
                                 reply_markup=InlineKeyboardMarkup(buttons))
        await update.callback_query.delete_message()
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_SLOT

async def cb_slot(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    slot = update.callback_query.data
    date, time = slot.split('|')
    spec = ctx.user_data['selected_spec']
    user = update.effective_user
    await update.callback_query.edit_message_text(
        f"Вы записались к {spec['ФИО']} на {date} {time}"
    )
    await ctx.bot.send_message(ADMIN_ID,
        f"Запись: {user.full_name} (id={user.id}) -> {spec['ФИО']} ({spec['Сфера']}, {spec['Регион']}) {date} {time}"
    )
    # (по желанию) удалить этот слот из таблицы:
    # rows = sheet.get_all_records()
    # for idx, r in enumerate(rows, 2): # первая строка — заголовок
    #     if r['ФИО']==spec['ФИО'] and r['Дата']==date and r['Время']==time:
    #         sheet.delete_row(idx)
    #         break
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
        CHOICE_SLOT:     [CallbackQueryHandler(cb_slot)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
application.add_handler(conv)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
