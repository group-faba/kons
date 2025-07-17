import os
import json
from flask import Flask, request, jsonify, abort
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import gspread
from gspread.exceptions import WorksheetNotFound
from datetime import datetime

# --- Flask healthcheck (чтобы Render не засыпал)
app = Flask(__name__)

@app.route('/', methods=['GET', 'HEAD'])
def health():
    return 'OK', 200

# 1) Сервисный ключ из ENV
creds_json = os.environ.get("GSPREAD_CREDENTIALS_JSON")
if not creds_json:
    raise RuntimeError("Missing GSPREAD_CREDENTIALS_JSON environment variable")
creds_dict = json.loads(creds_json)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets"
]
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)

# 2) Подключаемся к Drive и Sheets
drive_service = build("drive", "v3", credentials=creds)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(os.environ["SHEET_ID"])

# 3) Листы
experts_ws = sheet.worksheet("Эксперты")
users_ws   = sheet.worksheet("Users")

# Лист «Заявки» — если нет, создаём его автоматически
try:
    bookings_ws = sheet.worksheet("Заявки")
except WorksheetNotFound:
    bookings_ws = sheet.add_worksheet(title="Заявки", rows="1000", cols="5")

FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID")  # папка для фото
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

# Эндпоинт: регистрация пользователя (онбординг)
@app.route("/register-user", methods=["POST"])
def register_user():
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    city = data.get("city")
    if not name or not city:
        abort(400, "Missing required field")
    users_ws.append_row([datetime.now().isoformat(), name, city])
    return jsonify({"status": "ok"}), 200

# Эндпоинт: регистрация эксперта (multipart/form-data)
@app.route("/register-expert", methods=["POST"])
def register_expert():
    fio         = request.form.get("fio")
    city        = request.form.get("city")
    sphere      = request.form.get("sphere")
    description = request.form.get("description")
    if not all([fio, city, sphere, description]):
        abort(400, "Missing required field")

    photo_url = ""
    if "photo" in request.files:
        photo_file = request.files["photo"]
        file_metadata = {"name": photo_file.filename, "parents": [FOLDER_ID]}
        media = MediaIoBaseUpload(photo_file.stream, mimetype=photo_file.mimetype)
        file = drive_service.files().create(
            body=file_metadata, media_body=media, fields="id"
        ).execute()
        drive_service.permissions().create(
            fileId=file["id"], body={"type": "anyone", "role": "reader"}
        ).execute()
        photo_url = f"https://drive.google.com/uc?id={file['id']}"

    experts_ws.append_row([
        datetime.now().isoformat(),
        fio, city, sphere, description, photo_url
    ])
    return jsonify({"status": "ok", "photo_url": photo_url}), 200

# Эндпоинт: список экспертов
@app.route("/consultation-experts", methods=["GET"])
def get_experts():
    rows = experts_ws.get_all_records()
    return jsonify(rows), 200

# Эндпоинт: запись на консультацию (JSON)
@app.route("/book-expert", methods=["POST"])
def book_expert():
    data = request.get_json(silent=True) or {}
    fio         = data.get("fio")
    expert_name = data.get("expert_name")
    date_str    = data.get("date")
    time_str    = data.get("time")
    if not all([fio, expert_name, date_str, time_str]):
        abort(400, "Missing required field")

    bookings_ws.append_row([
        datetime.now().isoformat(),
        fio, expert_name, date_str, time_str
    ])
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(
        debug=True,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080))
    )
