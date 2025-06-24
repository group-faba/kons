import os
import json
import threading
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, ConversationHandler,
    ContextTypes, MessageHandler, filters
)

# ========== Конфигурация ==========
logging.basicConfig(level=logging.INFO)
TOKEN      = os.environ['TELEGRAM_TOKEN']
ADMIN_ID   = int(os.environ.get('ADMIN_CHAT_ID', '0'))
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT       = int(os.environ.get('PORT', '8080'))

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
gc    = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID).sheet1

def get_rows():
    return sheet.get_all_records()

# ========== Состояния Conversation ==========
CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC, CHOICE_TIME = range(4)
REGISTER_NAME, REGISTER_CITY, REGISTER_FIELD, REGISTER_DESC, REGISTER_CERT = range(4, 9)

# ========== /register — регистрация специалиста ==========
async def register_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите своё ФИО:")
    return REGISTER_NAME

async def register_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['name'] = update.message.text.strip()
    await update.message.reply_text("Введите город:")
    return REGISTER_CITY

async def register_city(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['city'] = update.message.text.strip()
    await update.message.reply_text("Введите сферу (например: Налоги, Бухгалтерия):")
    return REGISTER_FIELD

async def register_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['field'] = update.message.text.strip()
    await update.message.reply_text("Напишите короткое описание о себе:")
    return REGISTER_DESC

async def register_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['desc'] = update.message.text.strip()
    await update.message.reply_text("Пришлите ссылку на сертификат (или фото, или напишите - нет):")
    return REGISTER_CERT

async def register_cert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['cert'] = update.message.text.strip()
    tg_id = update.effective_user.id
    tg_username = update.effective_user.username or ""
    sheet_title = f"{ctx.user_data['name']}_{tg_id}"
    spreadsheet = gc.open_by_key(SHEET_ID)
    try:
        worksheet = spreadsheet.add_worksheet(title=sheet_title, rows="100", cols="10")
    except Exception as e:
        await update.message.reply_text("Ошибка при создании вкладки! Возможно, такая вкладка уже существует.")
        return ConversationHandler.END
    worksheet.append_row(["ФИО", "Город", "Сфера", "Описание", "Сертификат", "Telegram ID", "Username"])
    worksheet.append_row([
        ctx.user_data['name'],
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        ctx.user_data['cert'],
        str(tg_id),
        tg_username
    ])
    await update.message.reply_text(f"Ваша анкета успешно добавлена!\nТеперь ваши консультации будут падать на вкладку: {sheet_title}")
    return ConversationHandler.END

register_conv = ConversationHandler(
    entry_points=[CommandHandler('register', register_start)],
    states={
        REGISTER_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, register_name)],
        REGISTER_CITY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, register_city)],
        REGISTER_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_field)],
        REGISTER_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, register_desc)],
        REGISTER_CERT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, register_cert)],
    },
    fallbacks=[],
)

# ========== Основной функционал клиента ==========
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
    region = update.callback_query.data
    ctx.user_data['region'] = region
    rows = get_rows()
    seen = set(); buttons = []
    for r in rows:
        if r['Регион']==region and r['Сфера'] not in seen:
            seen.add(r['Сфера'])
            buttons.append([InlineKeyboardButton(r['Сфера'], callback_data=r['Сфера'])])
    await update.callback_query.edit_message_text(f'Регион: {region}\nВыберите сферу:',
                                                 reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_INDUSTRY

async def cb_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    industry = update.callback_query.data
    ctx.user_data['industry'] = industry
    rows = get_rows()
    specs = [r for r in rows if r['Регион']==ctx.user_data['region'] and r['Сфера']==industry]
    ctx.user_data['specs'] = specs
    buttons = [[InlineKeyboardButton(f"{s['ФИО']}", callback_data=str(i))] for i,s in enumerate(specs)]
    await update.callback_query.edit_message_text(f'Сфера: {industry}\nВыберите консультанта:',
                                                 reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    idx = int(update.callback_query.data)
    spec = ctx.user_data['specs'][idx]
    desc = spec.get('Описание', 'Нет описания')
    fio = spec['ФИО']
    cert = spec.get('Сертификат', None)
    buttons = [
        [InlineKeyboardButton("⬅️ Назад", callback_data='back')],
        [InlineKeyboardButton("✅ Выбрать этого специалиста", callback_data='choose')]
    ]
    msg = f"ФИО: {fio}\nОписание: {desc}"
    if cert and cert != 'нет':
        await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(buttons))
        await ctx.bot.send_photo(chat_id=update.effective_chat.id, photo=cert)
    else:
        await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(buttons))
    ctx.user_data['current_spec'] = idx
    return CHOICE_TIME

async def cb_choose(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    idx = ctx.user_data['current_spec']
    spec = ctx.user_data['specs'][idx]
    user = update.effective_user
    await ctx.bot.send_message(
        ADMIN_ID,
        f"Новая запись: {user.full_name} (id={user.id}) -> {spec['ФИО']} [{ctx.user_data['region']}/{spec['Сфера']}]"
    )
    await update.callback_query.edit_message_text("Ваша заявка отправлена!")
    return ConversationHandler.END

async def cb_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    industry = ctx.user_data['industry']
    rows = get_rows()
    specs = [r for r in rows if r['Регион']==ctx.user_data['region'] and r['Сфера']==industry]
    buttons = [[InlineKeyboardButton(f"{s['ФИО']}", callback_data=str(i))] for i,s in enumerate(specs)]
    await update.callback_query.edit_message_text(f'Сфера: {industry}\nВыберите консультанта:',
                                                 reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_SPEC

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Отменено.')
    return ConversationHandler.END

conv = ConversationHandler(
    entry_points=[CommandHandler('start', cmd_start)],
    states={
        CHOICE_REGION:   [CallbackQueryHandler(cb_region)],
        CHOICE_INDUSTRY: [CallbackQueryHandler(cb_industry)],
        CHOICE_SPEC:     [CallbackQueryHandler(cb_spec)],
        CHOICE_TIME: [
            CallbackQueryHandler(cb_back, pattern='back'),
            CallbackQueryHandler(cb_choose, pattern='choose')
        ],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)

# ========== Telegram Bot ==========
application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(conv)
application.add_handler(register_conv)

# ========== Запуск ==========
if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
