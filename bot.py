import os
import threading
import sqlite3
import logging
import time
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

# === Настройки из ENV ===
TELEGRAM_TOKEN    = os.getenv('TELEGRAM_TOKEN')
ADMIN_CHAT_ID     = int(os.getenv('ADMIN_CHAT_ID', '0'))
GOOGLE_CREDS_JSON = os.getenv('GOOGLE_CREDS_JSON')
REDIRECT_URI      = os.getenv('REDIRECT_URI')      # e.g. https://your.onrender.com/oauth2callback
DB_PATH           = os.getenv('DB_PATH', 'bot.db')
PORT              = int(os.getenv('PORT', '8080'))
SCOPES            = ['https://www.googleapis.com/auth/calendar.readonly']

# Запишем client_secrets.json из GOOGLE_CREDS_JSON
if GOOGLE_CREDS_JSON:
    with open('client_secrets.json', 'w') as f:
        f.write(GOOGLE_CREDS_JSON)

logging.basicConfig(level=logging.INFO)

# === SQLite-база для хранение OAuth-токенов ===
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

def get_creds(user_id) -> Credentials|None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        'SELECT token,refresh_token,token_uri,client_id,client_secret,scopes '
        'FROM tokens WHERE user_id=?', (str(user_id),)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return Credentials(
        token=row[0], refresh_token=row[1], token_uri=row[2],
        client_id=row[3], client_secret=row[4],
        scopes=row[5].split(',')
    )

# === Flask для OAuth и health check ===
app = Flask(__name__)

@app.route('/')
def health():
    return 'OK', 200

@app.route('/authorize')
def authorize():
    user = request.args.get('state')
    flow = Flow.from_client_secrets_file(
        'client_secrets.json', scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    auth_url, _ = flow.authorization_url(
        access_type='offline', include_granted_scopes='true', state=user
    )
    return redirect(auth_url)

@app.route('/oauth2callback')
def oauth2callback():
    user = request.args.get('state')
    flow = Flow.from_client_secrets_file(
        'client_secrets.json', scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    flow.fetch_token(authorization_response=request.url)
    save_creds(user, flow.credentials)
    return 'Календарь привязан! Вернитесь в Telegram и введите /start.'

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

# === Диалоговые состояния ===
CHOICE_REGION, CHOICE_INDUSTRY, CHOICE_SPEC, CHOICE_DATE, CHOICE_TIME = range(5)
REGIONS    = ['Москва','Санкт-Петербург','Краснодарский край']
INDUSTRIES = ['Психология','Финансы','Юриспруденция']
SPECIALISTS = [
    {'id':'spec1','name':'Анна Иванова','region':'Москва','industry':'Психология'},
    {'id':'spec2','name':'Игорь Петров','region':'Москва','industry':'Финансы'},
    {'id':'spec3','name':'Мария Сидорова','region':'Санкт-Петербург','industry':'Юриспруденция'}
]

# === Telegram Handlers ===
async def cmd_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    link = f"{REDIRECT_URI.replace('/oauth2callback','/authorize')}?state={uid}"
    await update.message.reply_text(f"Привяжи календарь:\n{link}")

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not get_creds(update.effective_user.id):
        await cmd_link(update, ctx)
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(r,callback_data=r)] for r in REGIONS]
    await update.message.reply_text("Выберите регион:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data['region'] = update.callback_query.data
    kb = [[InlineKeyboardButton(i,callback_data=i)] for i in INDUSTRIES]
    await update.callback_query.edit_message_text(
        f"Регион: {ctx.user_data['region']}\nВыберите отрасль:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOICE_INDUSTRY

async def cb_industry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data['industry'] = update.callback_query.data
    flt = [
        s for s in SPECIALISTS
        if s['region']==ctx.user_data['region'] and s['industry']==ctx.user_data['industry']
    ]
    kb = [[InlineKeyboardButton(s['name'],callback_data=s['id'])] for s in flt]
    await update.callback_query.edit_message_text("Выберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    spec = next(s for s in SPECIALISTS if s['id']==update.callback_query.data)
    ctx.user_data['spec'] = spec
    cal,step = DetailedTelegramCalendar(min_date=date.today(),locale='ru').build()
    await update.callback_query.edit_message_text("Выберите дату:", reply_markup=cal)
    return CHOICE_DATE

async def cb_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    result, key, step = DetailedTelegramCalendar(locale='ru').process(update.callback_query.data)
    if not result and key:
        await update.callback_query.edit_message_text(f"Выберите {LSTEP[step]}", reply_markup=key)
        return CHOICE_DATE
    ctx.user_data['date'] = result

    creds = get_creds(update.effective_user.id)
    svc = build('calendar','v3',credentials=creds)
    start = datetime.combine(result,datetime.min.time()).isoformat()+'Z'
    end   = (datetime.combine(result,datetime.min.time())+timedelta(days=1)).isoformat()+'Z'
    busy  = svc.freebusy().query({
        'timeMin':start,'timeMax':end,'items':[{'id':'primary'}]
    }).execute()['calendars']['primary']['busy']

    slots = []
    for h in range(9,18):
        t0 = datetime.combine(result,datetime.min.time())+timedelta(hours=h)
        if any(datetime.fromisoformat(b['start'].rstrip('Z'))<=t0<datetime.fromisoformat(b['end'].rstrip('Z')) for b in busy):
            continue
        slots.append(f"{h:02d}:00")

    kb = [[InlineKeyboardButton(t,callback_data=t)] for t in slots]
    await update.callback_query.edit_message_text("Выберите время:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOICE_TIME

async def cb_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    t0 = update.callback_query.data
    u = update.callback_query.from_user
    spec = ctx.user_data['spec']
    d = ctx.user_data['date'].strftime('%d.%m.%Y')
    await update.callback_query.edit_message_text(f"Запись подтверждена: {spec['name']}, {d} в {t0}")
    await ctx.bot.send_message(
        ADMIN_CHAT_ID,
        f"Новая запись от {u.full_name} (id={u.id}): {spec['name']}, {d} в {t0}"
    )
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

# ========== Основной запуск ==========
def main():
    init_db()

    # Flask в фоне
    threading.Thread(target=run_flask, daemon=True).start()

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Сбрасываем webhook перед polling
    application.bot.delete_webhook(drop_pending_updates=True)

    application.add_handler(CommandHandler('link_calendar', cmd_link))
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', cmd_start)],
        states={
            CHOICE_REGION:    [CallbackQueryHandler(cb_region)],
            CHOICE_INDUSTRY:  [CallbackQueryHandler(cb_industry)],
            CHOICE_SPEC:      [CallbackQueryHandler(cb_spec)],
            CHOICE_DATE:      [CallbackQueryHandler(cb_date)],
            CHOICE_TIME:      [CallbackQueryHandler(cb_time)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(conv)

    # Автоперезапуск при таймаутах
    while True:
        try:
            application.run_polling()
            break  # если polling лишён конфликтов, выйти
        except TimedOut:
            logging.warning("Polling timed out — перезапуск через 1 сек.")
            time.sleep(1)
        except Exception as e:
            logging.error(f"Unexpected error in polling: {e}")
            time.sleep(5)

if __name__ == '__main__':
    main()
