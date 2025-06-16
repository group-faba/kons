# web.py
import os
import logging
import sqlite3
from flask import Flask, request, redirect
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes
)
from telegram_bot_calendar import DetailedTelegramCalendar, LSTEP
from datetime import date, datetime, timedelta

# ========== Настройки ==========
TOKEN        = os.getenv('TELEGRAM_TOKEN')
ADMIN_ID     = int(os.getenv('ADMIN_CHAT_ID', '0'))
GOOGLE_JSON  = os.getenv('GOOGLE_CREDS_JSON')
CLIENT_FILE  = 'client_secrets.json'
REDIRECT_URI = os.getenv('REDIRECT_URI')
APP_URL      = os.getenv('APP_URL')
DB_PATH      = os.getenv('DB_PATH', 'bot.db')
PORT         = int(os.getenv('PORT', '8080'))
SCOPES       = ['https://www.googleapis.com/auth/calendar.readonly']

logging.basicConfig(level=logging.INFO)

if GOOGLE_JSON:
    with open(CLIENT_FILE, 'w') as f:
        f.write(GOOGLE_JSON)

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

def save_creds(uid, creds: Credentials):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('REPLACE INTO tokens VALUES (?,?,?,?,?,?,?)', (
        str(uid), creds.token, creds.refresh_token,
        creds.token_uri, creds.client_id, creds.client_secret,
        ','.join(creds.scopes)
    ))
    conn.commit()
    conn.close()

def get_creds(uid) -> Credentials | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        'SELECT token,refresh_token,token_uri,client_id,client_secret,scopes '
        'FROM tokens WHERE user_id=?', (str(uid),)
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
    user = request.args.get('state')
    flow = Flow.from_client_secrets_file(
        CLIENT_FILE, scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    flow.fetch_token(authorization_response=request.url)
    save_creds(user, flow.credentials)
    return 'Календарь привязан! Вернитесь в Telegram и отправьте /start.'

init_db()
application = ApplicationBuilder().token(TOKEN).build()

# ConversationHandler setup omitted for brevity...
# <здесь—все ваши CommandHandler, CallbackQueryHandler и ConversationHandler>

@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.process_update(update)
    return 'OK', 200

if __name__ == '__main__':
    application.bot.set_webhook(f"{APP_URL}/webhook")
    app.run(host='0.0.0.0', port=PORT)
