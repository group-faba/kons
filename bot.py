# bot.py
import os
import logging

from flask import Flask, request
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes
)

# ————— Настройки из ENV —————
TOKEN               = os.environ['TELEGRAM_TOKEN']
ADMIN_CHAT_ID       = int(os.environ['ADMIN_CHAT_ID'])
GSPREAD_CREDENTIALS = os.environ['GSPREAD_CREDENTIALS_JSON']
SHEET_ID            = os.environ['SHEET_ID']
PORT                = int(os.environ.get('PORT', 8080))
WEBAPP_URL          = os.environ.get('APP_URL')  # https://<your-service>.onrender.com

# ————— Состояния диалога —————
CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC = range(3)

# ————— Логирование —————
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ————— Гугл-таблица —————
# Даём gspread доступ к API и открываем нужный лист
scope = ['https://spreadsheets.google.com/feeds',
         'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(
    GSPREAD_CREDENTIALS, scope
)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID).sheet1

# Считываем все строки таблицы в список словарей
records = sheet.get_all_records()  # [{ 'ФИО':..., 'Регион':..., 'Сфера':..., 'Сертификат':...}, ...]

def unique_values(field, filter_by=None):
    """
    Список уникальных значений поля `field`.
    Если filter_by = {'Регион': 'Москва'}, то берём только записи, 
    в которых r['Регион']=='Москва'.
    """
    vals = []
    for r in records:
        if filter_by:
            k, v = next(iter(filter_by.items()))
            if r.get(k) != v:
                continue
        val = r.get(field)
        if val and val not in vals:
            vals.append(val)
    return vals

# ————— Telegram + Flask —————
app = Flask(__name__)
application = ApplicationBuilder().token(TOKEN).build()

# 1) /start — выбираем регион
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    regions = unique_values('Регион')
    kb = [[ InlineKeyboardButton(r, callback_data=r) ] for r in regions]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_REGION

# 2) регион → выбираем сферу
async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    region = update.callback_query.data
    ctx.user_data['region'] = region

    industries = unique_values('Сфера', {'Регион': region})
    kb = [[ InlineKeyboardButton(i, callback_data=i) ] for i in industries]
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nВыберите сферу:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOICE_INDUSTRY

# 3) сфера → выбираем консультанта
async def cb_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    industry = update.callback_query.data
    ctx.user_data['industry'] = industry

    # фильтруем records по регион+сфера
    opts = [
        r for r in records
        if r['Регион']==ctx.user_data['region'] and r['Сфера']==industry
    ]
    ctx.user_data['options'] = opts

    kb = [
        [ InlineKeyboardButton(r['ФИО'], callback_data=str(idx)) ]
        for idx, r in enumerate(opts)
    ]
    await update.callback_query.edit_message_text(
        f"Сфера: {industry}\nВыберите консультанта:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOICE_SPEC

# 4) консультант → показываем кнопку на сертификат и уведомляем админа
async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    idx = int(update.callback_query.data)
    spec = ctx.user_data['options'][idx]

    # Кнопка с URL на сертификат
    kb = [[ InlineKeyboardButton('Сертификат', url=spec['Сертификат']) ]]

    text = (
        f"Вы выбрали: {spec['ФИО']}\n"
        f"Регион: {ctx.user_data['region']}\n"
        f"Сфера: {ctx.user_data['industry']}"
    )
    await update.callback_query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(kb)
    )

    # Уведомляем администратора
    user = update.callback_query.from_user
    await application.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=(
            f"Новая запись от {user.full_name} (id={user.id}):\n"
            f"{spec['ФИО']} — {ctx.user_data['region']}, {ctx.user_data['industry']}"
        )
    )

    return ConversationHandler.END

# 5) Отмена
async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

# Регистрируем ConversationHandler
conv = ConversationHandler(
    entry_points=[CommandHandler('start', start)],
    states={
        CHOICE_REGION:   [CallbackQueryHandler(cb_region)],
        CHOICE_INDUSTRY: [CallbackQueryHandler(cb_industry)],
        CHOICE_SPEC:     [CallbackQueryHandler(cb_spec)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
application.add_handler(conv)

# Точка входа для вебхука
@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.process_update(update)
    return 'OK', 200

# Запускаем Flask + ставим вебхук (локально это не сработает, но на Render — да)
if __name__ == '__main__':
    # удаляем старый webhook, ставим новый
    import asyncio
    async def sethook():
        await application.bot.delete_webhook(drop_pending_updates=True)
        await application.bot.set_webhook(f"{WEBAPP_URL}/webhook")
    asyncio.run(sethook())

    # стартуем Flask
    app.run(host='0.0.0.0', port=PORT)
