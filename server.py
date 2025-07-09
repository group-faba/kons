import os
from flask import Flask, request, jsonify, abort
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import gspread
from datetime import datetime

app = Flask(__name__)

# — настройки Google Sheets и Drive —
SCOPES = ['https://www.googleapis.com/auth/drive',
          'https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'credentials.json'
FOLDER_ID = '<ТВОЙ_FOLDER_ID>'

creds = Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
drive_service = build('drive', 'v3', credentials=creds)
gc = gspread.authorize(creds)
sheet = gc.open("Консультации")         # название таблицы
experts_ws = sheet.worksheet("Эксперты") # лист «Эксперты»

def upload_file_to_drive(file_storage):
    # загружаем в папку на Google Drive
    file_metadata = {'name': file_storage.filename, 'parents': [FOLDER_ID]}
    media = MediaIoBaseUpload(file_storage.stream,
                              mimetype=file_storage.mimetype)
    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id'
    ).execute()
    drive_service.permissions().create(
        fileId=file['id'],
        body={'type': 'anyone', 'role': 'reader'}
    ).execute()
    return f"https://drive.google.com/uc?id={file['id']}"

@app.route('/register-expert', methods=['POST'])
def register_expert():
    # 1) Получаем текстовые поля из формы
    fio         = request.form.get('fio')
    city        = request.form.get('city')
    sphere      = request.form.get('sphere')
    description = request.form.get('description')

    if not all([fio, city, sphere, description]):
        abort(400, "Missing required field")

    # 2) Если есть файл — заливаем и получаем URL
    photo_url = ''
    if 'photo' in request.files:
        photo_file = request.files['photo']
        photo_url = upload_file_to_drive(photo_file)

    # 3) Записываем новую строку в Google Sheets
    experts_ws.append_row([
        datetime.now().isoformat(),
        fio, city, sphere, description, photo_url
    ])

    return jsonify({'status': 'ok', 'photo_url': photo_url}), 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
