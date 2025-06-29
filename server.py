import os
import json
import logging
from uuid import uuid4
from datetime import datetime
from flask import Flask, request, abort, jsonify
import gspread
from oauth2client.service_account import ServiceAccountCredentials

logging.basicConfig(level=logging.INFO)

# Google Sheets setup
SHEET_ID   = os.environ["SHEET_ID"]
CREDS_JSON = json.loads(os.environ["GSPREAD_CREDENTIALS_JSON"])
SCOPES     = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
creds      = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc         = gspread.authorize(creds)
ws         = gc.open_by_key(SHEET_ID).worksheet("Лист1")

app = Flask(__name__)

def now_str():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

@app.route("/")
def health():
    return "OK", 200

@app.route("/register", methods=["POST"])
def register():
    data = request.get_json(force=True)
    fio       = data.get("fio","").strip()
    city      = data.get("city","").strip()
    field     = data.get("field","").strip()
    desc      = data.get("desc","").strip()
    photo_url = data.get("photo_url","").strip()
    if not all([fio,city,field,desc]):
        abort(400, "Missing fields")
    expert_id = str(uuid4())
    row = [
        "expert", expert_id, now_str(),
        fio, city, field, desc, photo_url, "", ""
    ]
    ws.append_row(row)
    return jsonify(id=expert_id), 200

@app.route("/slots", methods=["POST"])
def set_slots():
    data      = request.get_json(force=True)
    expert_id = data.get("id","").strip()
    new_slots = data.get("slots",[])
    if not expert_id or not isinstance(new_slots,list):
        abort(400, "Bad payload")
    records = ws.get_all_records()
    for idx, rec in enumerate(records, start=2):
        if rec["id"] == expert_id and rec["role"]=="expert":
            existing = rec.get("slots","") or ""
            s = set(filter(None, [x.strip() for x in existing.split(";")]))
            s.update(new_slots)
            ws.update_cell(idx, 9, ";".join(sorted(s)))
            return jsonify(status="ok"), 200
    abort(404, "Expert not found")

@app.route("/experts", methods=["GET"])
def list_experts():
    city  = request.args.get("city","").strip()
    field = request.args.get("field","").strip()
    out = []
    for rec in ws.get_all_records():
        if rec["role"]=="expert" and rec["city"]==city and rec["field"]==field:
            out.append({
                "id":        rec["id"],
                "fio":       rec["fio"],
                "city":      rec["city"],
                "field":     rec["field"],
                "desc":      rec["desc"],
                "photo_url": rec["photo_url"]
            })
    return jsonify(experts=out), 200

@app.route("/experts/<expert_id>/slots", methods=["GET"])
def get_slots(expert_id):
    for rec in ws.get_all_records():
        if rec["id"]==expert_id and rec["role"]=="expert":
            raw = rec.get("slots","") or ""
            slots = [s for s in raw.split(";") if s]
            return jsonify(slots=slots), 200
    abort(404, "Expert not found")

@app.route("/book", methods=["POST"])
def book_slot():
    data      = request.get_json(force=True)
    expert_id = data.get("id","").strip()
    client_fio  = data.get("fio","").strip()
    client_city = data.get("city","").strip()
    slot        = data.get("slot","").strip()
    if not all([expert_id, client_fio, client_city, slot]):
        abort(400, "Missing fields")

    # 1) удалить слот у эксперта
    records = ws.get_all_records()
    for idx, rec in enumerate(records, start=2):
        if rec["id"]==expert_id and rec["role"]=="expert":
            s = [x for x in (rec.get("slots","") or "").split(";") if x and x!=slot]
            ws.update_cell(idx, 9, ";".join(s))
            break

    # 2) добавить строку-лог бронирования
    ws.append_row([
        "booking", "", now_str(),
        client_fio, client_city, "", "", "", "", slot
    ])
    return jsonify(status="booked"), 200

if __name__=="__main__":
    port = int(os.environ.get("PORT",8080))
    app.run(host="0.0.0.0", port=port)
