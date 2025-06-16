# bot.py

import os, json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, ConversationHandler,
    ContextTypes
)

# —– Конфигурация из ENV —–
TOKEN       = os.environ['TELEGRAM_TOKEN']
SHEET_ID    = os.environ['SHEET_ID']
CREDS_JSON  = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
APP_URL     = os.environ['APP_URL']      # https://<your-service>.onrender.com
PORT        = int(os.environ.get('PORT','8080'))

# —– Инициализация gspread —–
SCOPES = ['https://spreadsheets.google.com/feeds',
          'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc    = gspread.authorize(creds)
ws    = gc.open_by_key(SHEET_ID).sheet1
rows  = ws.get_all_records()

# —– Conversation states —–
CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC = range(3)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    regions = sorted({r['Регион'] for r in rows if r['Регион']})
    kb = [[InlineKeyboardButton(r, callback_data=r)] for r in regions]
    await update.message.reply_text("Выберите регион:", 
                                    reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sel = update.callback_query.data
    ctx.user_data['region'] = sel
    industries = sorted({r['Сфера'] for r in rows 
                         if r['Регион']==sel})
    kb = [[InlineKeyboardButton(i, callback_data=i)] for i in industries]
    await update.callback_query.edit_message_text(
        f"Регион: {sel}\nВыберите сферу:", 
        reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_INDUSTRY

async def cb_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sel = update.callback_query.data
    ctx.user_data['industry'] = sel
    specs = [r for r in rows
             if r['Регион']==ctx.user_data['region']
             and r['Сфера']==sel]
    ctx.user_data['specs'] = specs
    kb = [[InlineKeyboardButton(s['ФИО'], 
            callback_data=str(i))] 
          for i,s in enumerate(specs)]
    await update.callback_query.edit_message_text(
        f"Сфера: {sel}\nВыберите консультанта:", 
        reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    idx  = int(update.callback_query.data)
    spec = ctx.user_data['specs'][idx]
    await update.callback_query.edit_message_text(f"Вы выбрали: {spec['ФИО']}")
    if cert := spec.get('Сертификат'):
        await ctx.bot.send_photo(chat_id=update.effective_chat.id, photo=cert)
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

# —– Сборка приложения —–
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

# —– Flask (webhook + health) —–
app = Flask(__name__)

@app.route('/')
def health(): 
    return 'OK', 200

@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.process_update(update)
    return 'OK', 200

# —– Точка входа —–
if __name__ == '__main__':
    # Убираем старый webhook и ставим новый
    application.bot.delete_webhook()
    application.bot.set_webhook(f"{APP_URL}/webhook")
    # Запускаем Flask на нужном порту
    app.run(host='0.0.0.0', port=PORT)
