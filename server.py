import os
from flask import Flask, request, jsonify, abort
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import gspread
from datetime import datetime

app = Flask(__name__)

# — Настройки доступа к Google API —
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]
SERVICE_ACCOUNT_FILE = 'credentials.json'
FOLDER_ID = '1Kw2gyUFNKpmWisk9QxA0_I7aJaoGVDth'

creds = Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
drive_service = build('drive', 'v3', credentials=creds)
gc = gspread.authorize(creds)
sheet = gc.open("Консультации")          # название таблицы
experts_ws = sheet.worksheet("Эксперты")  # лист с экспертами

# — Эндпоинт: регистрация эксперта (multipart/form-data) —
@app.route('/register-expert', methods=['POST'])
def register_expert():
    fio         = request.form.get('fio')
    city        = request.form.get('city')
    sphere      = request.form.get('sphere')
    description = request.form.get('description')

    if not all([fio, city, sphere, description]):
        abort(400, "Missing required field")

    photo_url = ''
    if 'photo' in request.files:
        photo_file = request.files['photo']
        # загружаем картинку на Drive и делаем её публичной
        file_metadata = {'name': photo_file.filename, 'parents': [FOLDER_ID]}
        media = MediaIoBaseUpload(photo_file.stream, mimetype=photo_file.mimetype)
        file = drive_service.files().create(
            body=file_metadata, media_body=media, fields='id'
        ).execute()
        drive_service.permissions().create(
            fileId=file['id'],
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
        photo_url = f"https://drive.google.com/uc?id={file['id']}"

    # записываем в Google Sheets
    experts_ws.append_row([
        datetime.now().isoformat(),
        fio, city, sphere, description, photo_url
    ])

    return jsonify({'status': 'ok', 'photo_url': photo_url}), 200

# — Новый эндпоинт: возвращает список экспертов из таблицы —
@app.route('/consultation-experts', methods=['GET'])
def get_experts():
    # получаем все строки в виде списка словарей
    rows = experts_ws.get_all_records()  
    return jsonify(rows), 200

if __name__ == '__main__':
    # запускаем на 0.0.0.0:8080 (или порт из env)
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
