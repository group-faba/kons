import os
import json
from flask import Flask, request, abort, jsonify
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2.service_account import Credentials
import io

# --- Google Sheets & Drive Setup
SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
SCOPES_DRIVE = [
    "https://www.googleapis.com/auth/drive"
]

CREDS_JSON = json.loads(os.environ["GSPREAD_CREDENTIALS_JSON"])
SHEET_ID = os.environ["SHEET_ID"]

creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID)

drive_creds = Credentials.from_service_account_info(CREDS_JSON, scopes=SCOPES_DRIVE)
drive_service = build("drive", "v3", credentials=drive_creds)

app = Flask(__name__)

FOLDER_ID = "11Um24bBsSM-ikD8al7Re9p7TWkSIAVMd"  # <-- Поставь свой ID

def upload_photo_to_drive(file_storage):
    file_metadata = {
        "name": file_storage.filename,
        "parents": [FOLDER_ID]
    }
    media = MediaIoBaseUpload(
        io.BytesIO(file_storage.read()),
        mimetype=file_storage.mimetype
    )
    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id"
    ).execute()
    drive_service.permissions().create(
        fileId=file["id"],
        body={"type": "anyone", "role": "reader"}
    ).execute()
    return f"https://drive.google.com/uc?id={file['id']}"

@app.route('/')
def health():
    return "OK", 200

@app.route('/register-expert', methods=['POST'])
def register_expert():
    fio = request.form.get("fio")
    city = request.form.get("city")
    sphere = request.form.get("sphere")
    description = request.form.get("description")
    photo_url = ""
    
    # Проверяем есть ли фото
    if "photo" in request.files:
        photo = request.files["photo"]
        photo_url = upload_photo_to_drive(photo)
    
    if not fio or not city or not sphere or not description:
        abort(400, "Missing required field")

    worksheet = sheet.worksheet("Эксперты")
    worksheet.append_row([
        datetime.now().isoformat(),
        fio,
        city,
        sphere,
        description,
        photo_url
    ])
    return jsonify({"status": "ok", "photo_url": photo_url}), 200

@app.route('/book-expert', methods=['POST'])
def book_expert():
    data = request.json
    fio = data.get("fio")
    expert_name = data.get("expert_name")
    date = data.get("date")
    time = data.get("time")

    if not fio or not expert_name or not date or not time:
        abort(400, "Missing required field")

    worksheet = sheet.worksheet("Заявки")
    worksheet.append_row([datetime.now().isoformat(), fio, expert_name, date, time])
    return {"status": "ok"}, 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
