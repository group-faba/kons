import os
import json
from flask import Flask, request, jsonify, abort
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import gspread
from gspread.exceptions import WorksheetNotFound
from datetime import datetime

# ========== Flask app для healthcheck и эндпоинтов ==========
app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
def health():
    return "OK", 200

# ========== Чтение сервисного ключа из ENV ==========
creds_json = os.environ.get("GSPREAD_CREDENTIALS_JSON")
if not creds_json:
    raise RuntimeError("Missing GSPREAD_CREDENTIALS_JSON environment variable")
creds_dict = json.loads(creds_json)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets"
]

# ========== Авторизация Google ==========
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)

# ========== Подключение к Drive и Sheets ==========
drive_service = build("drive", "v3", credentials=creds)
gc            = gspread.authorize(creds)

SHEET_ID = os.environ.get("SHEET_ID")
if not SHEET_ID:
    raise RuntimeError("Missing SHEET_ID environment variable")

sheet = gc.open_by_key(SHEET_ID)

# ========== Листы ==========
experts_ws = sheet.worksheet("Эксперты")
users_ws   = sheet.worksheet("Users")

try:
    bookings_ws = sheet.worksheet("Заявки")
except WorksheetNotFound:
    bookings_ws = sheet.add_worksheet(title="Заявки", rows="1000", cols="5")

# ========== Папка на Drive для фото ==========
FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID")
if not FOLDER_ID:
    raise RuntimeError("Missing DRIVE_FOLDER_ID environment variable")

def upload_file_to_drive(file_storage):
    """Заливка файла на Google Drive и возврат публичного URL."""
    meta = {"name": file_storage.filename, "parents": [FOLDER_ID]}
    media = MediaIoBaseUpload(file_storage.stream, mimetype=file_storage.mimetype)
    f = drive_service.files().create(body=meta, media_body=media, fields="id").execute()
    drive_service.permissions().create(
        fileId=f["id"], body={"type": "anyone", "role": "reader"}
    ).execute()
    return f"https://drive.google.com/uc?id={f['id']}"

# ========== Эндпоинт: регистрация пользователя (onboarding) ==========
@app.route("/register-user", methods=["POST"])
def register_user():
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    city = data.get("city")
    if not name or not city:
        abort(400, "Missing required field")
    users_ws.append_row([datetime.now().isoformat(), name, city])
    return jsonify({"status": "ok"}), 200

# ========== Эндпоинт: регистрация эксперта (multipart/form-data) ==========
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
        photo = request.files["photo"]
        photo_url = upload_file_to_drive(photo)

    experts_ws.append_row([
        datetime.now().isoformat(),
        fio, city, sphere, description, photo_url
    ])
    return jsonify({"status": "ok", "photo_url": photo_url}), 200

# ========== Эндпоинт: список экспертов ==========
@app.route("/consultation-experts", methods=["GET"])
def get_experts():
    rows = experts_ws.get_all_records()
    return jsonify(rows), 200

# ========== Эндпоинт: запись на консультацию ==========
@app.route("/book-expert", methods=["POST"])
def book_expert():
    data        = request.get_json(silent=True) or {}
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

# ========== Запуск приложения ==========
if __name__ == "__main__":
    app.run(
        debug=True,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080))
    )
