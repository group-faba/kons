import os
import json
import threading
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes
)

# --- CONFIG ---
logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
ADMIN_ID   = int(os.environ.get('ADMIN_CHAT_ID', '0'))
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT       = int(os.environ.get('PORT', '8080'))

# --- Flask health-check ---
app = Flask(__name__)
@app.route('/')
def health():
    return 'OK', 200
def run_flask():
    app.run(host='0.0.0.0', port=PORT)

# --- Google Sheets ---
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc    = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID).sheet1

def get_rows():
    return sheet.get_all_records()

def update_status(fio, date, time):
    # Находит строку по ФИО, дате, времени и меняет статус на "занято"
    rows = sheet.get_all_values()
    for idx, row in enumerate(rows):
        if (row[0] == fio and row[5] == date and row[6] == time and row[7] != "занято"):
            sheet.update_cell(idx+1, 8, "занято")
            break

# --- Conversation states ---
CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC, CHOICE_TIME, CONFIRM = range(5)

# --- Handlers ---
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = get_rows()
    regions = sorted(set(r['Регион'] for r in rows if r['Регион']))
    buttons = [[InlineKeyboardButton(region, callback_data=region)] for region in regions]
    await update.message.reply_text('Выберите регион:', reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    region = update.callback_query.data
    ctx.user_data['region'] = region
    rows = get_rows()
    industries = sorted(set(r['Сфера'] for r in rows if r['Регион'] == region and r['Сфера']))
    buttons = [[InlineKeyboardButton(ind, callback_data=ind)] for ind in industries]
    await update.callback_query.edit_message_text(
        f'Регион: {region}\nВыберите сферу:', reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CHOICE_INDUSTRY

async def cb_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    industry = update.callback_query.data
    ctx.user_data['industry'] = industry
    rows = get_rows()
    # Все спецы по региону и сфере
    specs = [r for r in rows if r['Регион'] == ctx.user_data['region'] and r['Сфера'] == industry]
    ctx.user_data['specs'] = specs
    buttons = [[InlineKeyboardButton(s['ФИО'], callback_data=str(i))] for i, s in enumerate(specs)]
    await update.callback_query.edit_message_text(
        f'Сфера: {industry}\nВыберите специалиста:', reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    idx = int(update.callback_query.data)
    spec = ctx.user_data['specs'][idx]
    ctx.user_data['selected_spec'] = spec
    photo = spec.get('Сертификат', None)
    desc  = spec.get('Описание', '')
    fio   = spec['ФИО']
    text = f"Вы выбрали: {fio}\n\n{desc}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Назад", callback_data="back_spec")],
        [InlineKeyboardButton("Выбрать этого специалиста", callback_data="choose_spec")]
    ])
    if photo:
        await update.callback_query.message.delete()
        await update.callback_query.message.chat.send_photo(photo, caption=text, reply_markup=kb)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    return CONFIRM

async def cb_spec_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Вернуться к списку специалистов
    await update.callback_query.answer()
    specs = ctx.user_data['specs']
    buttons = [[InlineKeyboardButton(s['ФИО'], callback_data=str(i))] for i, s in enumerate(specs)]
    await update.callback_query.edit_message_text(
        'Выберите специалиста:', reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CHOICE_SPEC

async def cb_spec_choose(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    spec = ctx.user_data['selected_spec']
    fio = spec['ФИО']
    # Выбрать только свободные даты и время (где статус пусто)
    rows = get_rows()
    times = [
        (r['Дата'], r['Время'])
        for r in rows if r['ФИО'] == fio and not r.get('Статус')
    ]
    ctx.user_data['times'] = times
    if not times:
        await update.callback_query.edit_message_text('Нет свободного времени.')
        return ConversationHandler.END
    buttons = [
        [InlineKeyboardButton(f"{date} {time}", callback_data=f"{date}|{time}")]
        for date, time in times
    ]
    await update.callback_query.edit_message_text(
        "Выберите дату и время консультации:", reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CHOICE_TIME

async def cb_choose_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    selected = update.callback_query.data
    date, time = selected.split('|')
    ctx.user_data['chosen_time'] = (date, time)
    spec = ctx.user_data['selected_spec']
    # Обновить таблицу (занято), уведомить пользователя и админа
    update_status(spec['ФИО'], date, time)
    user = update.effective_user
    await update.callback_query.edit_message_text(
        f"Вы записались к {spec['ФИО']} на {date} {time}!"
    )
    await ctx.bot.send_message(
        ADMIN_ID,
        f"Новая запись: {user.full_name} (id={user.id}) -> {spec['ФИО']} [{spec['Регион']}/{spec['Сфера']}] на {date} {time}"
    )
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Отменено.')
    return ConversationHandler.END

# --- Telegram Bot ---
application = ApplicationBuilder().token(TOKEN).build()
conv = ConversationHandler(
    entry_points=[CommandHandler('start', cmd_start)],
    states={
        CHOICE_REGION:   [CallbackQueryHandler(cb_region)],
        CHOICE_INDUSTRY: [CallbackQueryHandler(cb_industry)],
        CHOICE_SPEC: [
            CallbackQueryHandler(cb_spec, pattern=r'^\d+$'),
            CallbackQueryHandler(cb_spec_back, pattern='^back_spec$'),
            CallbackQueryHandler(cb_spec_choose, pattern='^choose_spec$'),
        ],
        CONFIRM: [
            CallbackQueryHandler(cb_spec_back, pattern='^back_spec$'),
            CallbackQueryHandler(cb_spec_choose, pattern='^choose_spec$'),
        ],
        CHOICE_TIME: [
            CallbackQueryHandler(cb_choose_time, pattern=r'^\d{2}\.\d{2}\.\d{4}\|\d{2}:\d{2}$')
        ],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
application.add_handler(conv)

# --- Запуск ---
if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
