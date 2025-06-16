import os
import json
import logging
import threading
import gspread
from flask import Flask, request
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes
)

# ========== Логирование ==========
logging.basicConfig(level=logging.INFO)

# ========== Переменные окружения ==========
TELEGRAM_TOKEN           = os.getenv('TELEGRAM_TOKEN')
ADMIN_CHAT_ID            = int(os.getenv('ADMIN_CHAT_ID', '0'))
SHEET_ID                 = os.getenv('SHEET_ID')
GSPREAD_CREDENTIALS_JSON = os.getenv('GSPREAD_CREDENTIALS_JSON')
PORT                     = int(os.getenv('PORT', '8080'))

# ========== Flask для health-check ==========
app = Flask(__name__)

@app.route('/')
def health():
    return 'OK', 200

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

# ========== Google Sheets ==========
# Авторизация
creds_dict = json.loads(GSPREAD_CREDENTIALS_JSON)
# gspread expects oauth2client.ServiceAccountCredentials or google-auth
scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID).sheet1

# ========== Диалоговые состояния ==========
CHOICE_REGION, CHOICE_INDUSTRY = range(2)

# Загружаем данные из таблицы
rows = sheet.get_all_records()
REGIONS    = sorted({r['region'] for r in rows})
INDUSTRIES = sorted({r['industry'] for r in rows})

# ========== Handlers ==========
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    kb = [[InlineKeyboardButton(r, callback_data=r)] for r in REGIONS]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_REGION

async def choose_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data['region'] = update.callback_query.data
    kb = [[InlineKeyboardButton(i, callback_data=i)] for i in INDUSTRIES]
    await update.callback_query.edit_message_text(
        f"Регион: {ctx.user_data['region']}\nВыберите отрасль:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOICE_INDUSTRY

async def choose_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    region   = ctx.user_data['region']
    industry = update.callback_query.data
    # фильтрация
    rows = sheet.get_all_records()
    filtered = [r for r in rows if r['region']==region and r['industry']==industry]
    if not filtered:
        await update.callback_query.edit_message_text(
            "Консультанты не найдены. /start чтобы начать заново."
        )
        return ConversationHandler.END
    # строим сообщение
    text = 'Доступные консультанты:\n'
    kb = []
    for r in filtered:
        text += f"• {r['name']}\n"
        kb.append([InlineKeyboardButton("Сертификат", url=r['certificate_url'])])
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    # уведомляем админа
    u = update.effective_user
    await ctx.bot.send_message(
        ADMIN_CHAT_ID,
        f"Пользователь {u.full_name} (id={u.id}) выбрал {region} / {industry}"
    )
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

# ========== Запуск Telegram Bot ==========
application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
conv = ConversationHandler(
    entry_points=[CommandHandler('start', cmd_start)],
    states={
        CHOICE_REGION:    [CallbackQueryHandler(choose_region)],
        CHOICE_INDUSTRY:  [CallbackQueryHandler(choose_industry)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
application.add_handler(conv)

# Запускаем Flask и polling в одном процессе
if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
