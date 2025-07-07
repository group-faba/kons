# server.py

import os
import json
from flask import Flask, request, abort
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# --- Google Sheets Setup
SCOPES = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
CREDS_JSON = json.loads(os.environ["GSPREAD_CREDENTIALS_JSON"])
SHEET_ID = os.environ["SHEET_ID"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID)

app = Flask(__name__)

@app.route('/')
def health():
    return "OK", 200

@app.route('/register-expert', methods=['POST'])
def register_expert():
    data = request.json
    fio = data.get("fio")
    city = data.get("city")
    sphere = data.get("sphere")
    description = data.get("description")
    photo_url = data.get("photo_url", "")  # если будет фото

    if not fio or not city or not sphere or not description:
        abort(400, "Missing required field")

    worksheet = sheet.worksheet("Эксперты")  # Название листа
    worksheet.append_row([datetime.now().isoformat(), fio, city, sphere, description, photo_url])
    return {"status": "ok"}, 200

@app.route('/book-expert', methods=['POST'])
def book_expert():
    data = request.json
    fio = data.get("fio")
    expert_name = data.get("expert_name")
    date = data.get("date")
    time = data.get("time")

    if not fio or not expert_name or not date or not time:
        abort(400, "Missing required field")

    worksheet = sheet.worksheet("Заявки")  # Название листа
    worksheet.append_row([datetime.now().isoformat(), fio, expert_name, date, time])
    return {"status": "ok"}, 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
