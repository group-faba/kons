import os
import json
from flask import Flask, request, jsonify, abort
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import gspread
from datetime import datetime

app = Flask(__name__)

# — Получаем учётные данные из переменной окружения —
creds_json = os.environ.get("GSPREAD_CREDENTIALS_JSON")
if not creds_json:
    raise RuntimeError("Missing GSPREAD_CREDENTIALS_JSON environment variable")

creds_dict = json.loads(creds_json)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets"
]
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)

# — Настраиваем доступ к Drive и Sheets —
drive_service = build("drive", "v3", credentials=creds)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(os.environ["SHEET_ID"])       # или gc.open("Консультации")
experts_ws = sheet.worksheet("Эксперты")

FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID")  # ID папки в Google Drive
if not FOLDER_ID:
    raise RuntimeError("Missing DRIVE_FOLDER_ID environment variable")

def upload_file_to_drive(file_storage):
    meta = {"name": file_storage.filename, "parents": [FOLDER_ID]}
    media = MediaIoBaseUpload(file_storage.stream, mimetype=file_storage.mimetype)
    file = drive_service.files().create(body=meta, media_body=media, fields="id").execute()
    drive_service.permissions().create(
        fileId=file["id"], body={"type": "anyone", "role": "reader"}
    ).execute()
    return f"https://drive.google.com/uc?id={file['id']}"

# — Эндпоинт: регистрация эксперта —
@app.route("/register-expert", methods=["POST"])
def register_expert():
    fio         = request.form.get("fio")
    city        = request.form.get("city")
    sphere      = request.form.get("sphere")
    description = request.form.get("description")

    if not all([fio, city, sphere, description]):
        abort(400, "Missing required field")

    photo_url = ""
    if 'photo' in request.files and FOLDER_ID:
    photo_url = upload_file_to_drive(request.files['photo'])
else:
    photo_url = ''

    experts_ws.append_row([
        datetime.now().isoformat(),
        fio, city, sphere, description, photo_url
    ])
    return jsonify({"status": "ok", "photo_url": photo_url}), 200

# — Эндпоинт: список экспертов для консультаций —
@app.route("/consultation-experts", methods=["GET"])
def get_experts():
    records = experts_ws.get_all_records()
    return jsonify(records), 200

# — Эндпоинт: запись на консультацию —
@app.route("/book-expert", methods=["POST"])
def book_expert():
    data = request.get_json(silent=True) or {}
    fio         = data.get("fio")
    expert_name = data.get("expert_name")
    date        = data.get("date")
    time        = data.get("time")

    if not all([fio, expert_name, date, time]):
        abort(400, "Missing required field")

    bookings_ws = sheet.worksheet("Заявки")
    bookings_ws.append_row([
        datetime.now().isoformat(),
        fio, expert_name, date, time
    ])
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(
        debug=True,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080))
    )
