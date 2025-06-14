import os
from flask import Flask, redirect, request

app = Flask(__name__)

# health-check
@app.route('/')
def health_check():
    return 'OK', 200

# OAuth endpoints (скопируйте из bot.py все @app.route handlers)
from bot import CLIENT_SECRETS, SCOPES, REDIRECT_URI, init_db
from google_auth_oauthlib.flow import Flow
import sqlite3

init_db()

@app.route('/authorize')
def authorize():
    user_id = request.args.get('state')
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS, scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    auth_url, state = flow.authorization_url(
        access_type='offline', include_granted_scopes='true', state=user_id
    )
    return redirect(auth_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = request.args.get('state')
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS, scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    conn = sqlite3.connect(os.getenv('DB_PATH', 'bot.db'))
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
    return 'Календарь привязан. Вернитесь в Telegram и введите /start.'
