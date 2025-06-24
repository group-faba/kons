import os
import json
import logging
import threading
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes
)

# ========== Конфигурация ==========
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ['TELEGRAM_TOKEN']
ADMIN_ID = int(os.environ.get('ADMIN_CHAT_ID', '0'))
SHEET_ID = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT = int(os.environ.get('PORT', '8080'))

# ========== Flask для health-check ==========
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

# ========== Google Sheets ==========
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID).sheet1

def get_rows():
    return sheet.get_all_records()

# ========== Состояния Conversation ==========
CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC, CHOICE_SLOT = range(4)

# ========== Handlers ==========
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    rows = get_rows()
    seen = set()
    buttons = []
    for r in rows:
        if r['Регион'] and r['Регион'] not in seen:
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
        if r['Регион'] == region and r['Сфера'] and r['Сфера'] not in seen:
            seen.add(r['Сфера'])
            buttons.append([InlineKeyboardButton(r['Сфера'], callback_data=r['Сфера'])])
    await update.callback_query.edit_message_text(f'Регион: {region}\nВыберите сферу:', reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_INDUSTRY

async def cb_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    industry = update.callback_query.data
    ctx.user_data['industry'] = industry
    rows = get_rows()
    # Список специалистов по выбранным региону и сфере, сортировка по ФИО
    specs = [r for r in rows if r['Регион']==ctx.user_data['region'] and r['Сфера']==industry and r['ФИО']]
    ctx.user_data['specs'] = specs
    buttons = [[InlineKeyboardButton(s['ФИО'], callback_data=str(i))] for i,s in enumerate(specs)]
    await update.callback_query.edit_message_text(f'Сфера: {industry}\nВыберите специалиста:', reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    idx = int(update.callback_query.data)
    spec = ctx.user_data['specs'][idx]
    ctx.user_data['selected_spec'] = spec

    # Собираем доступные временные слоты этого специалиста
    rows = get_rows()
    slots = [
        (r['Дата'], r['Время'])
        for r in rows
        if r['ФИО'] == spec['ФИО']
           and r.get('Дата') and r.get('Время')
           and str(r.get('Статус', '')).lower() in ['свободно', '']
    ]
    ctx.user_data['slots'] = slots

    # Кнопки по слотам
    slot_buttons = []
    for i, (date, time) in enumerate(slots):
        slot_buttons.append([InlineKeyboardButton(f"{date} {time}", callback_data=f"{date}|{time}")])

    if not slot_buttons:
        slot_buttons = [[InlineKeyboardButton("Нет свободного времени", callback_data="no_slot")]]

    kb = InlineKeyboardMarkup(slot_buttons)

    # Отправляем фото и описание
    text = f"{spec['ФИО']}\n{spec.get('Описание','')}\nВыберите дату и время:"
    photo_url = spec.get('Сертификат')

    if photo_url:
        await ctx.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=photo_url,
            caption=text,
            reply_markup=kb
        )
    else:
        await ctx.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=kb
        )
    return CHOICE_SLOT

async def cb_slot(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    data = update.callback_query.data

    # Если нет слотов — ничего не делаем
    if data == 'no_slot':
        await update.callback_query.answer("Нет свободного времени", show_alert=True)
        return ConversationHandler.END

    date, time = data.split('|')
    spec = ctx.user_data['selected_spec']
    user = update.effective_user

    # Сохраняем в Google Sheets (меняем статус на 'Занято' у этой строки)
    rows = get_rows()
    for idx, r in enumerate(rows, start=2):  # сдвиг на 2: первая строка — заголовки, дальше данные
        if (r['ФИО'] == spec['ФИО']
            and r.get('Дата') == date
            and r.get('Время') == time):
            sheet.update_cell(idx, list(r.keys()).index('Статус') + 1, 'Занято')

    # Удаляем сообщение с кнопками
    try:
        await update.callback_query.delete_message()
    except Exception:
        pass

    # Сообщение пользователю
    await ctx.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Вы записались к {spec['ФИО']} на {date} {time}"
    )
    # Уведомление админу
    await ctx.bot.send_message(
        ADMIN_ID,
        f"Запись: {user.full_name} (id={user.id}) -> {spec['ФИО']} ({spec['Сфера']}, {spec['Регион']}) {date} {time}"
    )
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Отменено.')
    return ConversationHandler.END

# ========== Telegram Bot ==========
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

# ========== Запуск ==========
if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
