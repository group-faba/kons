import os
import json
import logging

def main():
    from flask import Flask, request
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        ApplicationBuilder, CommandHandler,
        CallbackQueryHandler, ConversationHandler,
        ContextTypes
    )

    # ========== Настройки из окружения ==========
    TOKEN = os.environ['TELEGRAM_TOKEN']
    ADMIN_CHAT_ID = int(os.environ['ADMIN_CHAT_ID'])
    SHEET_ID = os.environ['SHEET_ID']
    GSPREAD_JSON = os.environ['GSPREAD_CREDENTIALS_JSON']
    APP_URL = os.environ['APP_URL']        # e.g. https://<your-service>.onrender.com
    PORT = int(os.environ.get('PORT', '8080'))

    # ========== Логирование ==========
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # ========== Инициализация Google Sheets ==========
    # Парсим JSON-строку в словарь
    creds_dict = json.loads(GSPREAD_JSON)
    scopes = [
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive'
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scopes)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SHEET_ID).sheet1
    records = sheet.get_all_records()

    # ========== Conversation States ==========
    CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC = range(3)

    def unique_values(field, filter_by=None):
        seen = set()
        for row in records:
            if filter_by:
                key, val = next(iter(filter_by.items()))
                if row.get(key) != val:
                    continue
            value = row.get(field)
            if value and value not in seen:
                seen.add(value)
                yield value

    # ========== Handlers ==========
    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        kb = [[InlineKeyboardButton(r, callback_data=r)] for r in unique_values('Регион')]
        await update.message.reply_text('Выберите регион:', reply_markup=InlineKeyboardMarkup(kb))
        return CHOICE_REGION

    async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        await update.callback_query.answer()
        region = update.callback_query.data
        ctx.user_data['region'] = region
        kb = [[InlineKeyboardButton(i, callback_data=i)] for i in unique_values('Сфера', {'Регион': region})]
        await update.callback_query.edit_message_text(
            f'Регион: {region}\nВыберите сферу:',
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return CHOICE_INDUSTRY

    async def cb_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        await update.callback_query.answer()
        industry = update.callback_query.data
        ctx.user_data['industry'] = industry
        # Фильтруем консультантов
        options = [r for r in records if r['Регион']==ctx.user_data['region'] and r['Сфера']==industry]
        ctx.user_data['options'] = options
        kb = [[InlineKeyboardButton(opt['ФИО'], callback_data=str(idx))] for idx, opt in enumerate(options)]
        await update.callback_query.edit_message_text(
            f'Сфера: {industry}\nВыберите консультанта:',
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return CHOICE_SPEC

    async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        await update.callback_query.answer()
        idx = int(update.callback_query.data)
        spec = ctx.user_data['options'][idx]
        # Показываем кнопку-ссылку на сертификат
        kb = [[InlineKeyboardButton('Сертификат', url=spec['Сертификат'])]]
        await update.callback_query.edit_message_text(
            f"Вы выбрали: {spec['ФИО']}\n"
            f"Регион: {ctx.user_data['region']}\n"
            f"Сфера: {ctx.user_data['industry']}",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        # Уведомление админу
        user = update.callback_query.from_user
        await ctx.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                f"Новая заявка от {user.full_name} (id={user.id}):\n"
                f"{spec['ФИО']} — {ctx.user_data['region']}, {ctx.user_data['industry']}"
            )
        )
        return ConversationHandler.END

    async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        await update.message.reply_text('Отменено.')
        return ConversationHandler.END

    # ========== Настройка Telegram Bot ==========
    app_bot = ApplicationBuilder().token(TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CHOICE_REGION:   [CallbackQueryHandler(cb_region)],
            CHOICE_INDUSTRY: [CallbackQueryHandler(cb_industry)],
            CHOICE_SPEC:     [CallbackQueryHandler(cb_spec)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    app_bot.add_handler(conv)

    # ========== Flask Webhook Endpoint & Запуск ==========
    flask_app = Flask(__name__)

    @flask_app.route('/')
    def health():
        return 'OK', 200

    @flask_app.route('/webhook', methods=['POST'])
    def webhook():
        update = Update.de_json(request.get_json(force=True), app_bot.bot)
        app_bot.process_update(update)
        return 'OK', 200

    # Регистрируем webhook сразу
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(app_bot.bot.delete_webhook(drop_pending_updates=True))
    loop.run_until_complete(app_bot.bot.set_webhook(f"{APP_URL}/webhook"))

    # Запускаем Flask
    flask_app.run(host='0.0.0.0', port=PORT)


if __name__ == '__main__':
    main()
