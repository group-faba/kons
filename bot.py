# bot.py
import os
import logging
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ConversationHandler,
    CallbackQueryHandler, ContextTypes
)
from telegram_bot_calendar import DetailedTelegramCalendar, LSTEP
from datetime import date, datetime, timedelta
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ======================= Настройки =======================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
ADMIN_CHAT_ID  = int(os.getenv('ADMIN_CHAT_ID', '0'))
DB_PATH        = os.getenv('DB_PATH', 'bot.db')

if not TELEGRAM_TOKEN or not ADMIN_CHAT_ID:
    logging.error('TELEGRAM_TOKEN или ADMIN_CHAT_ID не заданы')
    exit(1)

logging.basicConfig(level=logging.INFO)

# ======================= База токенов =======================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
      CREATE TABLE IF NOT EXISTS tokens (
        user_id TEXT PRIMARY KEY,
        token TEXT,
        refresh_token TEXT,
        token_uri TEXT,
        client_id TEXT,
        client_secret TEXT,
        scopes TEXT
      )
    ''')
    conn.commit()
    conn.close()

def get_credentials(user_id: int) -> Credentials | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        'SELECT token,refresh_token,token_uri,client_id,client_secret,scopes '
        'FROM tokens WHERE user_id=?', (str(user_id),)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return Credentials(
        token=row[0],
        refresh_token=row[1],
        token_uri=row[2],
        client_id=row[3],
        client_secret=row[4],
        scopes=row[5].split(',')
    )

def generate_free_slots(user_id: int, date_selected: date) -> list[str]:
    creds = get_credentials(user_id)
    if not creds:
        return []
    service = build('calendar', 'v3', credentials=creds)
    start = datetime.combine(date_selected, datetime.min.time()).isoformat() + 'Z'
    end   = (datetime.combine(date_selected, datetime.min.time()) + timedelta(days=1)).isoformat() + 'Z'
    resp = service.freebusy().query({
        'timeMin': start, 'timeMax': end, 'items': [{'id':'primary'}]
    }).execute()
    busy = resp['calendars']['primary']['busy']
    slots = []
    for hour in range(9, 18):
        slot = datetime.combine(date_selected, datetime.min.time()) + timedelta(hours=hour)
        if any(
            datetime.fromisoformat(b['start'].rstrip('Z')) <= slot < datetime.fromisoformat(b['end'].rstrip('Z'))
            for b in busy
        ):
            continue
        slots.append(f"{hour:02d}:00")
    return slots

# ======================= Диалог бронирования =======================
CHOOSING_REGION, CHOOSING_INDUSTRY, CHOOSING_SPECIALIST, CHOOSING_DATE, CHOOSING_TIME = range(5)

REGIONS = ['Москва', 'Санкт-Петербург', 'Краснодарский край']
INDUSTRIES = ['Психология', 'Финансы', 'Юриспруденция']
SPECIALISTS = [
    {'id':'spec1','name':'Анна Иванова','region':'Москва','industry':'Психология'},
    {'id':'spec2','name':'Игорь Петров','region':'Москва','industry':'Финансы'},
    {'id':'spec3','name':'Мария Сидорова','region':'Санкт-Петербург','industry':'Юриспруденция'}
]

async def link_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Сначала привяжи календарь командой /link_calendar")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not get_credentials(user_id):
        await update.message.reply_text("Сначала привяжи календарь командой /link_calendar")
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(r, callback_data=r)] for r in REGIONS]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_REGION

async def handle_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    region = update.callback_query.data
    await update.callback_query.answer()
    context.user_data['region'] = region
    keyboard = [[InlineKeyboardButton(i, callback_data=i)] for i in INDUSTRIES]
    await update.callback_query.edit_message_text(f"Регион: {region}\nВыберите отрасль:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_INDUSTRY

async def handle_industry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    industry = update.callback_query.data
    await update.callback_query.answer()
    context.user_data['industry'] = industry
    region = context.user_data['region']
    filtered = [s for s in SPECIALISTS if s['region']==region and s['industry']==industry]
    if not filtered:
        await update.callback_query.edit_message_text("Консультанты не найдены.")
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(s['name'], callback_data=s['id'])] for s in filtered]
    await update.callback_query.edit_message_text(f"Отрасль: {industry}\nВыберите специалиста:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_SPECIALIST

async def handle_specialist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    spec_id = update.callback_query.data
    await update.callback_query.answer()
    spec = next(s for s in SPECIALISTS if s['id']==spec_id)
    context.user_data['specialist'] = spec
    calendar, step = DetailedTelegramCalendar(min_date=date.today(), locale='ru').build()
    await update.callback_query.edit_message_text("Выберите дату:", reply_markup=calendar)
    return CHOOSING_DATE

async def handle_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    result, key, step = DetailedTelegramCalendar(locale='ru').process(update.callback_query.data)
    if not result and key:
        await update.callback_query.edit_message_text(f"Выберите {LSTEP[step]}", reply_markup=key)
        return CHOOSING_DATE
    context.user_data['date'] = result
    slots = generate_free_slots(update.effective_user.id, result)
    keyboard = [[InlineKeyboardButton(t, callback_data=t)] for t in slots]
    await update.callback_query.edit_message_text("Выберите время:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_TIME

async def handle_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.callback_query.data
    await update.callback_query.answer()
    user = update.callback_query.from_user
    spec = context.user_data['specialist']
    date_sel = context.user_data['date'].strftime('%d.%m.%Y')
    await update.callback_query.edit_message_text(f"Запись подтверждена: {spec['name']}, {date_sel} в {t}")
    await context.bot.send_message(ADMIN_CHAT_ID, f"Новая запись от {user.full_name} (id={user.id}): {spec['name']}, {date_sel} в {t}")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler('link_calendar', link_calendar))
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CHOOSING_REGION:     [CallbackQueryHandler(handle_region)],
            CHOOSING_INDUSTRY:   [CallbackQueryHandler(handle_industry)],
            CHOOSING_SPECIALIST: [CallbackQueryHandler(handle_specialist)],
            CHOOSING_DATE:       [CallbackQueryHandler(handle_date)],
            CHOOSING_TIME:       [CallbackQueryHandler(handle_time)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    app.add_handler(conv)
    app.run_polling()

if __name__ == '__main__':
    main()
