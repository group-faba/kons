# bot.py
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
from telegram_bot_calendar import DetailedTelegramCalendar, LSTEP
from datetime import date, datetime, timedelta

# ========== Настройки ==========
TELEGRAM_TOKEN    = os.getenv('TELEGRAM_TOKEN')
ADMIN_CHAT_ID     = int(os.getenv('ADMIN_CHAT_ID', '0'))
CLIENT_FILE       = 'client_secrets.json'
REDIRECT_URI      = os.getenv('REDIRECT_URI')
DB_PATH           = os.getenv('DB_PATH', 'bot.db')
GOOGLE_CREDS_JSON = os.getenv('GOOGLE_CREDS_JSON')
PORT              = int(os.getenv('PORT', '8080'))
SCOPES            = ['https://www.googleapis.com/auth/calendar.readonly']

if GOOGLE_CREDS_JSON:
    with open(CLIENT_FILE, 'w') as f:
        f.write(GOOGLE_CREDS_JSON)

logging.basicConfig(level=logging.INFO)

# ========== БД для токенов ==========
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
      CREATE TABLE IF NOT EXISTS tokens (
        user_id TEXT PRIMARY KEY,
        token TEXT, refresh_token TEXT,
        token_uri TEXT, client_id TEXT,
        client_secret TEXT, scopes TEXT
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
      'SELECT token,refresh_token,token_uri,client_id,client_secret,scopes FROM tokens WHERE user_id=?',
      (str(user_id),)
    ).fetchone()
    conn.close()
    if not row: return None
    return Credentials(
      token=row[0],
      refresh_token=row[1],
      token_uri=row[2],
      client_id=row[3],
      client_secret=row[4],
      scopes=row[5].split(',')
    )

# ========== Flask для OAuth & health-check ==========
app = Flask(__name__)

@app.route('/')
def health():
    return 'OK', 200

@app.route('/authorize')
def authorize():
    user = request.args.get('state')
    flow = Flow.from_client_secrets_file(
        CLIENT_FILE, scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    auth_url, _ = flow.authorization_url(
        access_type='offline', include_granted_scopes='true', state=user
    )
    return redirect(auth_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = request.args.get('state')
    flow = Flow.from_client_secrets_file(
        CLIENT_FILE, scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    flow.fetch_token(authorization_response=request.url)
    save_creds(state, flow.credentials)
    return 'Календарь привязан! Вернитесь в бота и введите /start.'

# ========== Telegram-бот через polling ==========
CHOOSING_REGION, CHOOSING_INDUSTRY, CHOOSING_SPECIALIST, CHOOSING_DATE, CHOOSING_TIME = range(5)
REGIONS = ['Москва','Санкт-Петербург','Краснодарский край']
INDUSTRIES = ['Психология','Финансы','Юриспруденция']
SPECIALISTS = [
  {'id':'spec1','name':'Анна Иванова','region':'Москва','industry':'Психология'},
  {'id':'spec2','name':'Игорь Петров','region':'Москва','industry':'Финансы'},
  {'id':'spec3','name':'Мария Сидорова','region':'Санкт-Петербург','industry':'Юриспруденция'}
]

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    link = f"{REDIRECT_URI.replace('/oauth2callback','/authorize')}?state={uid}"
    await update.message.reply_text(f"Привяжи календарь:\n{link}")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not get_creds(update.effective_user.id):
        await cmd_link(update, context)
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(r,callback_data=r)] for r in REGIONS]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_REGION

async def handle_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    r = update.callback_query.data
    context.user_data['region'] = r
    kb = [[InlineKeyboardButton(i,callback_data=i)] for i in INDUSTRIES]
    await update.callback_query.edit_message_text(f"Регион: {r}\nВыберите отрасль:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_INDUSTRY

async def handle_industry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    i = update.callback_query.data
    context.user_data['industry'] = i
    filtered = [s for s in SPECIALISTS if s['region']==context.user_data['region'] and s['industry']==i]
    kb = [[InlineKeyboardButton(s['name'],callback_data=s['id'])] for s in filtered]
    await update.callback_query.edit_message_text(f"Отрасль: {i}\nВыберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_SPECIALIST

async def handle_specialist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    sid = update.callback_query.data
    spec = next(s for s in SPECIALISTS if s['id']==sid)
    context.user_data['spec'] = spec
    cal,step = DetailedTelegramCalendar(min_date=date.today(),locale='ru').build()
    await update.callback_query.edit_message_text("Выберите дату:", reply_markup=cal)
    return CHOOSING_DATE

async def handle_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    res,key,step = DetailedTelegramCalendar(locale='ru').process(update.callback_query.data)
    if not res and key:
        await update.callback_query.edit_message_text(f"Выберите {LSTEP[step]}", reply_markup=key)
        return CHOOSING_DATE
    context.user_data['date'] = res
    # получим слоты
    creds = get_creds(update.effective_user.id)
    service = build('calendar','v3',credentials=creds)
    start = datetime.combine(res,datetime.min.time()).isoformat()+'Z'
    end   = (datetime.combine(res,datetime.min.time())+timedelta(days=1)).isoformat()+'Z'
    busy = service.freebusy().query({'timeMin':start,'timeMax':end,'items':[{'id':'primary'}]}).execute()['calendars']['primary']['busy']
    slots=[]
    for h in range(9,18):
        t = datetime.combine(res,datetime.min.time())+timedelta(hours=h)
        if any(datetime.fromisoformat(b['start'].rstrip('Z'))<=t<datetime.fromisoformat(b['end'].rstrip('Z')) for b in busy): continue
        slots.append(f"{h:02d}:00")
    kb=[[InlineKeyboardButton(s,callback_data=s)] for s in slots]
    await update.callback_query.edit_message_text("Выберите время:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING_TIME

async def handle_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.callback_query.data
    u = update.callback_query.from_user
    spec = context.user_data['spec']
    d = context.user_data['date'].strftime('%d.%m.%Y')
    await update.callback_query.edit_message_text(f"Запись: {spec['name']}, {d} в {t} — подтверждена.")
    await context.bot.send_message(ADMIN_CHAT_ID, f"Новая запись от {u.full_name} (id={u.id}): {spec['name']}, {d} в {t}")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

def main():
    init_db()
    # запуск Flask в отдельном потоке
    threading.Thread(target=run_flask,daemon=True).start()

    app_bot = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app_bot.add_handler(CommandHandler('link_calendar', cmd_link))
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
    app_bot.add_handler(conv)
    app_bot.run_polling()

if __name__ == '__main__':
    main()
