import os
import logging
from datetime import date, datetime, timedelta
from flask import Flask
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, MessageHandler, filters
)
from telegram_bot_calendar import DetailedTelegramCalendar, LSTEP
from googleapiclient.discovery import build
from oauth2client.service_account import ServiceAccountCredentials

# ========== КОНФИГУРАЦИЯ ==========
TOKEN = os.getenv('TELEGRAM_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
GOOGLE_CREDS = os.getenv('GOOGLE_CREDS_JSON', 'service_account.json')
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

if not TOKEN or not ADMIN_CHAT_ID:
    logging.error('Переменные TELEGRAM_TOKEN или ADMIN_CHAT_ID не заданы')
    exit(1)

ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)
PORT = int(os.getenv('PORT', '5000'))  # Render передаёт PORT

# Инициализируем Google Calendar API
creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDS, SCOPES)
calendar_service = build('calendar', 'v3', credentials=creds)

# ==================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# === ДАННЫЕ КОСУЛЬТАНТОВ ===
# Каждый консультант должен иметь свой calendar_id в Google Calendar
REGIONS = ['Москва', 'Санкт-Петербург', 'Краснодарский край']
INDUSTRIES = ['Психология', 'Финансы', 'Юриспруденция']
SPECIALISTS = [
    {
        'id': 'spec1', 'name': 'Анна Иванова', 'region': 'Москва', 'industry': 'Психология',
        'calendar_id': os.getenv('CAL_ID_SPEC1', 'anna@example.com')
    },
    {
        'id': 'spec2', 'name': 'Игорь Петров', 'region': 'Москва', 'industry': 'Финансы',
        'calendar_id': os.getenv('CAL_ID_SPEC2', 'igor@example.com')
    },
    {
        'id': 'spec3', 'name': 'Мария Сидорова', 'region': 'Санкт-Петербург', 'industry': 'Юриспруденция',
        'calendar_id': os.getenv('CAL_ID_SPEC3', 'maria@example.com')
    }
]

# Состояния ConversationHandler
(
    CHOOSING_REGION,
    CHOOSING_INDUSTRY,
    CHOOSING_SPECIALIST,
    CHOOSING_DATE,
    CHOOSING_TIME
) = range(5)

# ===== Утилиты для работы с расписанием =====
def get_busy_intervals(calendar_id: str, date_selected: date) -> list:
    start_day = datetime.combine(date_selected, datetime.min.time()).isoformat() + 'Z'
    end_day = (datetime.combine(date_selected, datetime.min.time()) + timedelta(days=1)).isoformat() + 'Z'
    body = {
        'timeMin': start_day,
        'timeMax': end_day,
        'items': [{'id': calendar_id}]
    }
    resp = calendar_service.freebusy().query(body=body).execute()
    return resp['calendars'][calendar_id]['busy']


def generate_free_slots(calendar_id: str, date_selected: date) -> list[str]:
    busy = get_busy_intervals(calendar_id, date_selected)
    slots = []
    for hour in range(9, 18):  # слоты с 09:00 до 17:00
        slot_start = datetime.combine(date_selected, datetime.min.time()) + timedelta(hours=hour)
        # проверяем, не попадает ли слот в занятый интервал
        if any(
            datetime.fromisoformat(b['start'].rstrip('Z')) <= slot_start < datetime.fromisoformat(b['end'].rstrip('Z'))
            for b in busy
        ):
            continue
        slots.append(f"{hour:02d}:00")
    return slots

# ===== Логика Telegram-бота =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [[InlineKeyboardButton(r, callback_data=r)] for r in REGIONS]
    await update.message.reply_text('Выберите регион:', reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_REGION

async def handle_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    region = update.callback_query.data
    await update.callback_query.answer()
    context.user_data['region'] = region
    keyboard = [[InlineKeyboardButton(i, callback_data=i)] for i in INDUSTRIES]
    await update.callback_query.edit_message_text(
        f'Регион: {region}\nТеперь выберите отрасль:',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING_INDUSTRY

async def handle_industry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    industry = update.callback_query.data
    await update.callback_query.answer()
    context.user_data['industry'] = industry
    region = context.user_data['region']
    filtered = [s for s in SPECIALISTS if s['region']==region and s['industry']==industry]
    if not filtered:
        await update.callback_query.edit_message_text('Специалистов не найдено.')
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(s['name'], callback_data=s['id'])] for s in filtered]
    await update.callback_query.edit_message_text(
        f'Регион: {region}\nОтрасль: {industry}\nВыберите специалиста:',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING_SPECIALIST

async def handle_specialist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    spec_id = update.callback_query.data
    await update.callback_query.answer()
    spec = next(s for s in SPECIALISTS if s['id']==spec_id)
    context.user_data['specialist'] = spec
    calendar, step = DetailedTelegramCalendar(min_date=date.today(), locale='ru').build()
    await update.callback_query.edit_message_text('Выберите дату:', reply_markup=calendar)
    return CHOOSING_DATE

async def handle_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    result, key, step = DetailedTelegramCalendar(locale='ru').process(update.callback_query.data)
    if not result and key:
        await update.callback_query.edit_message_text(
            f'Выберите {LSTEP[step]}', reply_markup=key
        )
        return CHOOSING_DATE
    if result:
        context.user_data['date'] = result
        spec = context.user_data['specialist']
        slots = generate_free_slots(spec['calendar_id'], result)
        if not slots:
            await update.callback_query.edit_message_text('Свободных слотов нет.')
            return ConversationHandler.END
        keyboard = [[InlineKeyboardButton(t, callback_data=t)] for t in slots]
        await update.callback_query.edit_message_text(
            'Выберите время:', reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CHOOSING_TIME

async def handle_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    time_sel = update.callback_query.data
    await update.callback_query.answer()
    user = update.callback_query.from_user
    spec = context.user_data['specialist']
    date_sel = context.user_data['date'].strftime('%d.%m.%Y')
    await update.callback_query.edit_message_text(
        f'Ваша запись: {spec["name"]}, {date_sel} в {time_sel} подтверждена.'
    )
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=(f'Новая запись от {user.full_name} (id={user.id}): '  
              f'{spec["name"]}, {date_sel} в {time_sel}')
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Отменено.')
    return ConversationHandler.END

# ===== Функция для запуска Flask (открывает порт) =====
def run_flask():
    app = Flask(__name__)
    @app.route('/')
    def health():
        return 'OK', 200
    app.run(host='0.0.0.0', port=PORT)

# ===== MAIN =====
def main():
    # Запускаем Flask в фоне
    threading.Thread(target=run_flask, daemon=True).start()
    # Стартуем бота (polling) в главном потоке
    app = ApplicationBuilder().token(TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CHOOSING_REGION: [CallbackQueryHandler(handle_region)],
            CHOOSING_INDUSTRY: [CallbackQueryHandler(handle_industry)],
            CHOOSING_SPECIALIST: [CallbackQueryHandler(handle_specialist)],
            CHOOSING_DATE: [CallbackQueryHandler(handle_date)],
            CHOOSING_TIME: [CallbackQueryHandler(handle_time)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    app.add_handler(conv)
    app.run_polling()

if __name__ == '__main__':
    main()
