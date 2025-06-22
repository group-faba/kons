import os
import json
import gspread
from flask import Flask, request
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ConversationHandler, ContextTypes

TOKEN = os.environ['TELEGRAM_TOKEN']
SHEET_ID = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
ADMIN_ID = int(os.environ.get('ADMIN_CHAT_ID', '0'))
APP_URL = os.environ['APP_URL']

# Sheets
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID).sheet1
rows = sheet.get_all_records()

CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC = range(3)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
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
    specs = [r for r in rows if r['Регион'] == ctx.user_data['region'] and r['Сфера'] == industry]
    ctx.user_data['specs'] = specs
    buttons = [[InlineKeyboardButton(s['ФИО'], callback_data=str(i))] for i, s in enumerate(specs)]
    await update.callback_query.edit_message_text(f'Сфера: {industry}\nВыберите консультанта:', reply_markup=InlineKeyboardMarkup(buttons))
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    idx = int(update.callback_query.data)
    spec = ctx.user_data['specs'][idx]
    await update.callback_query.edit_message_text(f"Вы выбрали: {spec['ФИО']}")
    url = spec.get('Сертификат')
    if url:
        await ctx.bot.send_photo(chat_id=update.effective_chat.id, photo=url)
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Отменено.')
    return ConversationHandler.END

from telegram.ext import Application

bot_app = ApplicationBuilder().token(TOKEN).build()
conv = ConversationHandler(
    entry_points=[CommandHandler('start', cmd_start)],
    states={
        CHOICE_REGION: [CallbackQueryHandler(cb_region)],
        CHOICE_INDUSTRY: [CallbackQueryHandler(cb_industry)],
        CHOICE_SPEC: [CallbackQueryHandler(cb_spec)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
bot_app.add_handler(conv)

# Flask app
app = Flask(__name__)

@app.route("/", methods=["GET"])
def health(): return "OK", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    bot_app.update_queue.put_nowait(update)
    return "ok", 200

if __name__ == "__main__":
    import threading
    port = int(os.environ.get('PORT', 8080))
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=port), daemon=True).start()
    bot_app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path="webhook",
        webhook_url=APP_URL + "/webhook"
    )
