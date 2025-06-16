# web.py
import os
import logging
import sqlite3
from flask import Flask, request, redirect
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
)
from telegram_bot_calendar import DetailedTelegramCalendar, LSTEP
from datetime import date, datetime, timedelta
from googleapiclient.discovery import build

# ========== Настройки ==========
TOKEN        = os.getenv('TELEGRAM_TOKEN')
ADMIN_ID     = int(os.getenv('ADMIN_CHAT_ID', '0'))
CLIENT_FILE  = 'client_secrets.json'
REDIRECT_URI = os.getenv('REDIRECT_URI')    # https://<ваш-домен>/oauth2callback
APP_URL      = os.getenv('APP_URL')         # https://<ваш-домен>
DB_PATH      = os.getenv('DB_PATH', 'bot.db')
PORT         = int(os.getenv('PORT', '8080'))
SCOPES       = ['https://www.googleapis.com/auth/calendar.readonly']

# Запись client_secrets.json из ENV
creds_env = os.getenv('GOOGLE_CREDS_JSON')
if creds_env:
    with open(CLIENT_FILE, 'w') as f:
        f.write(creds_env)

logging.basicConfig(level=logging.INFO)

# ========== Инициализация БД ==========
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

# ========== Flask-приложение ==========
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
        access_type='offline',
        include_granted_scopes='true',
        state=user
    )
    return redirect(auth_url)

@app.route('/oauth2callback')
def oauth2callback():
    user = request.args.get('state')
    flow = Flow.from_client_secrets_file(
        CLIENT_FILE, scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    conn = sqlite3.connect(DB_PATH)
    conn.execute('REPLACE INTO tokens VALUES (?,?,?,?,?,?,?)', (
        user,
        creds.token,
        creds.refresh_token,
        creds.token_uri,
        creds.client_id,
        creds.client_secret,
        ','.join(creds.scopes)
    ))
    conn.commit()
    conn.close()
    return 'Календарь успешно привязан! Вернитесь в Telegram и введите /start.'

# ========== Telegram Webhook Setup ==========
# Создаём приложение Telegram
application = ApplicationBuilder().token(TOKEN).build()
bot = application.bot

# Хелперы для Google Calendar
def get_creds(user_id):
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

def generate_free_slots(user_id, dt: date):
    creds = get_creds(user_id)
    if not creds:
        return []
    service = build('calendar','v3',credentials=creds)
    start = datetime.combine(dt, datetime.min.time()).isoformat()+'Z'
    end   = (datetime.combine(dt, datetime.min.time())+timedelta(days=1)).isoformat()+'Z'
    resp = service.freebusy().query({
        'timeMin': start, 'timeMax': end, 'items': [{'id':'primary'}]
    }).execute()
    busy = resp['calendars']['primary']['busy']
    slots = []
    for h in range(9,18):
        s = datetime.combine(dt, datetime.min.time())+timedelta(hours=h)
        if any(datetime.fromisoformat(b['start'].rstrip('Z'))<=s<datetime.fromisoformat(b['end'].rstrip('Z')) for b in busy):
            continue
        slots.append(f"{h:02d}:00")
    return slots

# Диалоговые состояния и данные
REGIONS     = ['Москва','Санкт-Петербург','Краснодарский край']
INDUSTRIES  = ['Психология','Финансы','Юриспруденция']
SPECIALISTS = [
    {'id':'spec1','name':'Анна Иванова','region':'Москва','industry':'Психология'},
    {'id':'spec2','name':'Игорь Петров','region':'Москва','industry':'Финансы'},
    {'id':'spec3','name':'Мария Сидорова','region':'Санкт-Петербург','industry':'Юриспруденция'}
]
CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC, CHOICE_DATE, CHOICE_TIME = range(5)

# Handlers
async def cmd_link(update: Update, context):
    uid = update.effective_user.id
    url = f"{REDIRECT_URI.replace('/oauth2callback','/authorize')}?state={uid}"
    await update.message.reply_text(f"Привяжи календарь по ссылке:\n{url}")

async def cmd_start(update: Update, context):
    uid = update.effective_user.id
    if not get_creds(uid):
        return await cmd_link(update, context)
    kb = [[InlineKeyboardButton(r,callback_data=r)] for r in REGIONS]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_region(update: Update, context):
    r = update.callback_query.data
    await update.callback_query.answer()
    context.user_data['region'] = r
    kb = [[InlineKeyboardButton(i,callback_data=i)] for i in INDUSTRIES]
    await update.callback_query.edit_message_text(f"Регион: {r}\nВыберите отрасль:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_industry(update: Update, context):
    i = update.callback_query.data
    await update.callback_query.answer()
    context.user_data['industry'] = i
    filtered = [s for s in SPECIALISTS if s['region']==context.user_data['region'] and s['industry']==i]
    kb = [[InlineKeyboardButton(s['name'],callback_data=s['id'])] for s in filtered]
    await update.callback_query.edit_message_text(f"Отрасль: {i}\nВыберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_spec(update: Update, context):
    sid = update.callback_query.data
    await update.callback_query.answer()
    spec = next(s for s in SPECIALISTS if s['id']==sid)
    context.user_data['spec'] = spec
    cal, step = DetailedTelegramCalendar(min_date=date.today(), locale='ru').build()
    await update.callback_query.edit_message_text("Выберите дату:", reply_markup=cal)

async def cb_date(update: Update, context):
    result, key, step = DetailedTelegramCalendar(locale='ru').process(update.callback_query.data)
    if not result and key:
        await update.callback_query.edit_message_text(f"Выберите {LSTEP[step]}", reply_markup=key)
        return
    context.user_data['date'] = result
    slots = generate_free_slots(update.effective_user.id, result)
    kb = [[InlineKeyboardButton(t,callback_data=t)] for t in slots]
    await update.callback_query.edit_message_text("Выберите время:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_time(update: Update, context):
    t = update.callback_query.data
    await update.callback_query.answer()
    u = update.callback_query.from_user
    spec = context.user_data['spec']
    d = context.user_data['date'].strftime('%d.%m.%Y')
    await update.callback_query.edit_message_text(f"Запись: {spec['name']}, {d} в {t} — подтверждена.")
    await bot.send_message(ADMIN_ID, f"Новая запись от {u.full_name} (id={u.id}): {spec['name']}, {d} в {t}")

# Регистрируем хендлеры
application.add_handler(CommandHandler('link_calendar', cmd_link))
application.add_handler(CommandHandler('start', cmd_start))
application.add_handler(CallbackQueryHandler(cb_region,   pattern='^Москва|Санкт-Петербург|Краснодарский край$'))
application.add_handler(CallbackQueryHandler(cb_industry, pattern='^Психология|Финансы|Юриспруденция$'))
application.add_handler(CallbackQueryHandler(cb_spec,     pattern='^spec[123]$'))
application.add_handler(CallbackQueryHandler(cb_date,     pattern='^\\d{1,2};\\d{1,2};\\d{4}$'))
application.add_handler(CallbackQueryHandler(cb_time,     pattern='^\\d{2}:00$'))

# Webhook endpoint
@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    application.dispatcher.process_update(update)
    return 'OK', 200

 init_db()
bot.set_webhook(f"{APP_URL}/webhook")
