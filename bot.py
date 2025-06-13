import os
import json
import logging
import threading
import sqlite3
from flask import Flask, redirect, request
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, MessageHandler, filters
)
from telegram_bot_calendar import DetailedTelegramCalendar, LSTEP
from datetime import date, datetime, timedelta

# ========== ЗАГРУЗКА СЕКРЕТОВ GOOGLE OAUTH ==========
creds_json = os.getenv('GOOGLE_CREDS_JSON')
if creds_json:
    with open('client_secrets.json', 'w') as f:
        f.write(creds_json)

# ========== КОНФИГУРАЦИЯ ==========
TELEGRAM_TOKEN    = os.getenv('TELEGRAM_TOKEN')
ADMIN_CHAT_ID     = int(os.getenv('ADMIN_CHAT_ID', '0'))
CLIENT_SECRETS    = os.getenv('CLIENT_SECRETS_FILE', 'client_secrets.json')
REDIRECT_URI      = os.getenv('REDIRECT_URI')  # https://your-service.onrender.com/oauth2callback
SCOPES            = ['https://www.googleapis.com/auth/calendar.readonly']
DB_PATH           = os.getenv('DB_PATH', 'bot.db')
PORT              = int(os.getenv('PORT', '8080'))

if not TELEGRAM_TOKEN or not ADMIN_CHAT_ID or not REDIRECT_URI:
    logging.error('Не заданы TELEGRAM_TOKEN, ADMIN_CHAT_ID или REDIRECT_URI')
    exit(1)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ========== ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
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

# ========== FLASK-СЕРВЕР ДЛЯ OAUTH ==========
app = Flask(__name__)

@app.route('/authorize')
def authorize():
    user_id = request.args.get('state')
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        state=user_id
    )
    return redirect(auth_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = request.args.get('state')  # telegram user_id
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('REPLACE INTO tokens VALUES (?,?,?,?,?,?,?)', (
        state,
        creds.token,
        creds.refresh_token,
        creds.token_uri,
        creds.client_id,
        creds.client_secret,
        ','.join(creds.scopes)
    ))
    conn.commit()
    conn.close()
    return 'Календарь успешно привязан. Можете вернуться к боту.'

# ========== GOOGLE CALENDAR HELPERS ==========
def get_credentials(user_id: str) -> Credentials | None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        'SELECT token,refresh_token,token_uri,client_id,client_secret,scopes '
        'FROM tokens WHERE user_id=?', (str(user_id),)
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    token, refresh, token_uri, client_id, client_secret, scopes = row
    return Credentials(
        token=token,
        refresh_token=refresh,
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes.split(',')
    )

def generate_free_slots(user_id: int, date_selected: date) -> list[str]:
    creds = get_credentials(user_id)
    if not creds:
        return []
    service = build('calendar', 'v3', credentials=creds)
    start = datetime.combine(date_selected, datetime.min.time()).isoformat() + 'Z'
    end   = (datetime.combine(date_selected, datetime.min.time()) + timedelta(days=1)).isoformat() + 'Z'
    body = {'timeMin': start, 'timeMax': end, 'items': [{'id': 'primary'}]}
    resp = service.freebusy().query(body=body).execute()
    busy = resp['calendars']['primary']['busy']
    slots = []
    for hour in range(9, 18):  # слоты 09:00–17:00
        slot = datetime.combine(date_selected, datetime.min.time()) + timedelta(hours=hour)
        if any(
            datetime.fromisoformat(b['start'].rstrip('Z')) <= slot < datetime.fromisoformat(b['end'].rstrip('Z'))
            for b in busy
        ):
            continue
        slots.append(f"{hour:02d}:00")
    return slots

# ========== BOT LOGIC ==========
# Conversation states
CHOOSING_REGION, CHOOSING_INDUSTRY, CHOOSING_SPECIALIST, CHOOSING_DATE, CHOOSING_TIME = range(5)

REGIONS = ['Москва', 'Санкт-Петербург', 'Краснодарский край']
INDUSTRIES = ['Психология', 'Финансы', 'Юриспруденция']
SPECIALISTS = [
    {'id':'spec1','name':'Анна Иванова','region':'Москва','industry':'Психология'},
    {'id':'spec2','name':'Игорь Петров','region':'Москва','industry':'Финансы'},
    {'id':'spec3','name':'Мария Сидорова','region':'Санкт-Петербург','industry':'Юриспруденция'},
]

async def link_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    link = f"{REDIRECT_URI.replace('/oauth2callback','/authorize')}?state={user_id}"
    await update.message.reply_text(
        f"Привяжи календарь, перейдя по ссылке:\n{link}"
    )

async def start_booking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not get_credentials(update.effective_user.id):
        await update.message.reply_text("Сначала привяжи календарь командой /link_calendar")
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(r, callback_data=r)] for r in REGIONS]
    await update.message.reply_text("Выбери регион:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_REGION

async def handle_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    region = update.callback_query.data
    await update.callback_query.answer()
    context.user_data['region'] = region
    keyboard = [[InlineKeyboardButton(i, callback_data=i)] for i in INDUSTRIES]
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nВыбери отрасль:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
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
    await update.callback_query.edit_message_text(
        f"Регион: {region}\nОтрасль: {industry}\nВыбери специалиста:", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING_SPECIALIST

async def handle_specialist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    spec_id = update.callback_query.data
    await update.callback_query.answer()
    spec = next(s for s in SPECIALISTS if s['id']==spec_id)
    context.user_data['specialist'] = spec
    calendar, step = DetailedTelegramCalendar(min_date=date.today(), locale='ru').build()
    await update.callback_query.edit_message_text("Выбери дату:", reply_markup=calendar)
    return CHOOSING_DATE

async def handle_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    result, key, step = DetailedTelegramCalendar(locale='ru').process(update.callback_query.data)
    if not result and key:
        await update.callback_query.edit_message_text(f"Выбери {LSTEP[step]}", reply_markup=key)
        return CHOOSING_DATE
    context.user_data['date'] = result
    slots = generate_free_slots(update.effective_user.id, result)
    if not slots:
        await update.callback_query.edit_message_text("Свободных слотов нет.")
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(t, callback_data=t)] for t in slots]
    await update.callback_query.edit_message_text("Выбери время:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_TIME

async def handle_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    time_sel = update.callback_query.data
    await update.callback_query.answer()
    user = update.callback_query.from_user
    spec = context.user_data['specialist']
    date_sel = context.user_data['date'].strftime('%d.%m.%Y')
    await update.callback_query.edit_message_text(
        f"Запись подтверждена:\n{spec['name']}, {date_sel} в {time_sel}"
    )
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=(f"Новая запись от {user.full_name} (id={user.id}): "
              f"{spec['name']}, {date_sel} в {time_sel}")
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

# ========== ЗАПУСК ==========
def run_flask():
    app.run(host='0.0.0.0', port=PORT)

def main():
    init_db()
    threading.Thread(target=run_flask, daemon=True).start()
    bot = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    bot.add_handler(CommandHandler('link_calendar', link_calendar))
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start_booking)],
        states={
            CHOOSING_REGION:     [CallbackQueryHandler(handle_region)],
            CHOOSING_INDUSTRY:   [CallbackQueryHandler(handle_industry)],
            CHOOSING_SPECIALIST: [CallbackQueryHandler(handle_specialist)],
            CHOOSING_DATE:       [CallbackQueryHandler(handle_date)],
            CHOOSING_TIME:       [CallbackQueryHandler(handle_time)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    bot.add_handler(conv)
    bot.run_polling()

if __name__ == '__main__':
    main()
