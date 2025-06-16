import os
import logging
import threading
import sqlite3
from flask import Flask, redirect, request
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ConversationHandler,
    CallbackQueryHandler, ContextTypes
)
from telegram.error import TimedOut
from telegram_bot_calendar import DetailedTelegramCalendar, LSTEP
from datetime import date, datetime, timedelta
import time

# ========== ЗАГРУЗКА СЕКРЕТОВ ==========
GOOGLE_CREDS_JSON = os.getenv('GOOGLE_CREDS_JSON')
CLIENT_FILE       = 'client_secrets.json'
if GOOGLE_CREDS_JSON:
    with open(CLIENT_FILE, 'w') as f:
        f.write(GOOGLE_CREDS_JSON)

# ========== КОНФИГУРАЦИЯ ==========
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
ADMIN_CHAT_ID  = int(os.getenv('ADMIN_CHAT_ID', '0'))
REDIRECT_URI   = os.getenv('REDIRECT_URI')     # https://<your>/oauth2callback
PORT           = int(os.getenv('PORT', '8080'))
DB_PATH        = os.getenv('DB_PATH', 'bot.db')
SCOPES         = ['https://www.googleapis.com/auth/calendar.readonly']

if not TELEGRAM_TOKEN or not ADMIN_CHAT_ID or not REDIRECT_URI:
    logging.error("Не заданы TELEGRAM_TOKEN, ADMIN_CHAT_ID или REDIRECT_URI")
    exit(1)

logging.basicConfig(level=logging.INFO)

# ========== БД ==========
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

def save_creds(user_id, creds: Credentials):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('REPLACE INTO tokens VALUES (?,?,?,?,?,?,?)', (
        str(user_id),
        creds.token,
        creds.refresh_token,
        creds.token_uri,
        creds.client_id,
        creds.client_secret,
        ','.join(creds.scopes)
    ))
    conn.commit()
    conn.close()

def get_creds(user_id) -> Credentials | None:
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

# ========== Flask для OAuth & health ==========
app = Flask(__name__)

@app.route('/')
def health():
    return 'OK', 200

@app.route('/authorize')
def authorize():
    user = request.args.get('state')
    flow = Flow.from_client_secrets_file(CLIENT_FILE, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, _ = flow.authorization_url(access_type='offline', include_granted_scopes='true', state=user)
    return redirect(auth_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = request.args.get('state')
    flow = Flow.from_client_secrets_file(CLIENT_FILE, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    flow.fetch_token(authorization_response=request.url)
    save_creds(state, flow.credentials)
    return 'Календарь привязан! Вернитесь в бот и введите /start.'

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

# ========== Telegram-polling ==========
CHOOSING_REGION, CHOOSING_INDUSTRY, CHOOSING_SPECIALIST, CHOOSING_DATE, CHOOSING_TIME = range(5)

REGIONS    = ['Москва', 'Санкт-Петербург', 'Краснодарский край']
INDUSTRIES = ['Психология', 'Финансы', 'Юриспруденция']
SPECIALISTS = [
    {'id':'spec1','name':'Анна Иванова','region':'Москва','industry':'Психология'},
    {'id':'spec2','name':'Игорь Петров','region':'Москва','industry':'Финансы'},
    {'id':'spec3','name':'Мария Сидорова','region':'Санкт-Петербург','industry':'Юриспруденция'}
]

async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    link = f"{REDIRECT_URI.replace('/oauth2callback','/authorize')}?state={uid}"
    await update.message.reply_text(f"Привяжи календарь по ссылке:\n{link}")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not get_creds(update.effective_user.id):
        await cmd_link(update, context)
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(r, callback_data=r)] for r in REGIONS]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_REGION

async def handle_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    region = update.callback_query.data
    await update.callback_query.answer()
    context.user_data['region'] = region
    kb = [[InlineKeyboardButton(i, callback_data=i)] for i in INDUSTRIES]
    await update.callback_query.edit_message_text(f"Регион: {region}\nВыберите отрасль:", reply_markup=InlineKeyboardMarkup(kb))
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
    kb = [[InlineKeyboardButton(s['name'], callback_data=s['id'])] for s in filtered]
    await update.callback_query.edit_message_text("Выберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_SPECIALIST

async def handle_specialist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    sid = update.callback_query.data
    await update.callback_query.answer()
    spec = next(s for s in SPECIALISTS if s['id']==sid)
    context.user_data['spec'] = spec
    cal, step = DetailedTelegramCalendar(min_date=date.today(), locale='ru').build()
    await update.callback_query.edit_message_text("Выберите дату:", reply_markup=cal)
    return CHOOSING_DATE

async def handle_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    result, key, step = DetailedTelegramCalendar(locale='ru').process(update.callback_query.data)
    if not result and key:
        await update.callback_query.edit_message_text(f"Выберите {LSTEP[step]}", reply_markup=key)
        return CHOOSING_DATE
    context.user_data['date'] = result
    creds = get_creds(update.effective_user.id)
    service = build('calendar','v3',credentials=creds)
    start = datetime.combine(result, datetime.min.time()).isoformat()+'Z'
    end   = (datetime.combine(result, datetime.min.time())+timedelta(days=1)).isoformat()+'Z'
    busy = service.freebusy().query({'timeMin':start,'timeMax':end,'items':[{'id':'primary'}]}).execute()['calendars']['primary']['busy']
    slots=[]
    for h in range(9,18):
        slot = datetime.combine(result, datetime.min.time())+timedelta(hours=h)
        if any(datetime.fromisoformat(b['start'].rstrip('Z'))<=slot<datetime.fromisoformat(b['end'].rstrip('Z')) for b in busy):
            continue
        slots.append(f"{h:02d}:00")
    kb = [[InlineKeyboardButton(t,callback_data=t)] for t in slots]
    await update.callback_query.edit_message_text("Выберите время:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_TIME

async def handle_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    time_sel = update.callback_query.data
    await update.callback_query.answer()
    user = update.callback_query.from_user
    spec = context.user_data['spec']
    date_sel = context.user_data['date'].strftime('%d.%m.%Y')
    await update.callback_query.edit_message_text(f"Запись подтверждена: {spec['name']}, {date_sel} в {time_sel}")
    await context.bot.send_message(ADMIN_CHAT_ID, f"Новая запись от {user.full_name} (id={user.id}): {spec['name']}, {date_sel} в {time_sel}")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

def main():
    init_db()
    # Flask в фоне
    threading.Thread(target=run_flask, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    # сброс webhook, если был
    app.bot.delete_webhook(drop_pending_updates=True)

    app.add_handler(CommandHandler('link_calendar', cmd_link))
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', cmd_start)],
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

    # авто-перезапуск при таймаутах
    while True:
        try:
            app.run_polling()
        except TimedOut:
            logging.warning("Timeout polling — перезапускаем через 1 сек.")
            time.sleep(1)
        except Exception as e:
            logging.error(f"Ошибка polling: {e}")
            time.sleep(5)

if __name__ == '__main__':
    main()
