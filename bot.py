# bot.py
import os, json, threading
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, ConversationHandler,
    ContextTypes
)

# === Конфиг из ENV ===
TOKEN       = os.environ['TELEGRAM_TOKEN']
SHEET_ID    = os.environ['SHEET_ID']
CREDS_JSON  = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT        = int(os.environ.get('PORT', '8080'))

# === Инициализация gspread ===
SCOPES = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive'
]
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc    = gspread.authorize(creds)
ws    = gc.open_by_key(SHEET_ID).sheet1
rows  = ws.get_all_records()  # список словарей

# === Состояния Conversation ===
CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC = range(3)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    regions = sorted({r['Регион'] for r in rows if r['Регион']})
    kb = [[InlineKeyboardButton(r, callback_data=r)] for r in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sel = update.callback_query.data
    await update.callback_query.answer()
    ctx.user_data['region'] = sel
    industries = sorted({r['Сфера'] for r in rows if r['Регион']==sel})
    kb = [[InlineKeyboardButton(i, callback_data=i)] for i in industries]
    await update.callback_query.edit_message_text(
        f"Регион: {sel}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOICE_INDUSTRY

async def cb_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sel = update.callback_query.data
    await update.callback_query.answer()
    ctx.user_data['industry'] = sel
    # фильтруем консультантов
    specs = [
        r for r in rows
        if r['Регион']==ctx.user_data['region']
        and r['Сфера']==sel
    ]
    ctx.user_data['specs'] = specs
    kb = [[InlineKeyboardButton(s['ФИО'], callback_data=str(i))]
          for i,s in enumerate(specs)]
    await update.callback_query.edit_message_text(
        f"Сфера: {sel}\nВыберите консультанта:", reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    idx = int(update.callback_query.data)
    spec = ctx.user_data['specs'][idx]
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(f"Вы выбрали: {spec['ФИО']}")
    # отправляем картинку сертификата
    url = spec.get('Сертификат')
    if url:
        await ctx.bot.send_photo(chat_id=update.effective_chat.id, photo=url)
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

# === Собираем Telegram-приложение ===
application = ApplicationBuilder().token(TOKEN).build()
conv = ConversationHandler(
    entry_points=[CommandHandler('start', cmd_start)],
    states={
        CHOICE_REGION:   [CallbackQueryHandler(cb_region)],
        CHOICE_INDUSTRY: [CallbackQueryHandler(cb_industry)],
        CHOICE_SPEC:     [CallbackQueryHandler(cb_spec)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
application.add_handler(conv)

# === Flask для health-check ===
flask_app = Flask(__name__)
@flask_app.route('/')
def health(): return 'OK', 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

# === Точка входа ===
if __name__ == '__main__':
    # запускаем Flask в отдельном потоке
    threading.Thread(target=run_flask, daemon=True).start()
    # запускаем бота в режиме polling
    application.run_polling()
