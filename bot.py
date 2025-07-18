import os
import json
import logging
import gspread
from google.oauth2.service_account import Credentials
from flask import Flask
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
from datetime import datetime, timedelta

# ——— Настройка логирования ———
logging.basicConfig(level=logging.INFO)

# ——— Параметры из ENV ———
TOKEN        = os.environ['TELEGRAM_TOKEN']
SHEET_ID     = os.environ['SHEET_ID']
CREDS_JSON   = os.environ['GSPREAD_CREDENTIALS_JSON']
PORT         = int(os.environ.get('PORT', '8080'))

# ——— GSpread авторизация ———
creds_dict = json.loads(CREDS_JSON)
SCOPES = ['https://www.googleapis.com/auth/spreadsheets',
          'https://www.googleapis.com/auth/drive']
creds   = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc      = gspread.authorize(creds)
sheet   = gc.open_by_key(SHEET_ID)

# ——— Листы Google Sheets ———
experts_ws  = sheet.worksheet("Эксперты")
users_ws    = sheet.worksheet("Users")
try:
    bookings_ws = sheet.worksheet("Заявки")
except gspread.exceptions.WorksheetNotFound:
    bookings_ws = sheet.add_worksheet(title="Заявки", rows="1000", cols="5")

# ——— Flask healthcheck ———
app = Flask(__name__)
@app.route('/', methods=['GET', 'HEAD'])
def health():
    return "OK", 200

# ——— HELPERS ———
def get_specialists():
    rows = experts_ws.get_all_records()
    specs = []
    for idx, row in enumerate(rows, start=2):
        spec = dict(row)
        spec['row_num'] = idx
        slots_str = experts_ws.cell(idx, 8).value or ""
        spec['slots']   = [s.strip() for s in slots_str.split(';') if s.strip()]
        specs.append(spec)
    return specs

def get_specialist_row(tg_id):
    records = experts_ws.get_all_records()
    for i, row in enumerate(records, start=2):
        if str(row.get("Telegram ID","")) == str(tg_id):
            return experts_ws, i, row
    return None, None, None

# ——— Константы состояний ———
(
    # регистрация эксперта
    REG_NAME, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO,
    # выбор слотов у эксперта
    TIME_DATE, TIME_SELECT,
    # выбор консультации
    CH_REGION, CH_FIELD, CH_SPEC, CH_DATE, CH_TIME
) = range(11)

# ——— ОБРАБОТЧИК /start ———
async def start_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Консультации", callback_data="consult")],
        [InlineKeyboardButton("✏️ Регистрация эксперта", callback_data="register")],
    ])
    if update.message:
        await update.message.reply_text("Выберите действие:", reply_markup=kb)
    else:
        await update.callback_query.message.edit_text("Выберите действие:", reply_markup=kb)
    return ConversationHandler.END  # просто выводим меню

# ——— CALLBACK: “Консультации” — запускаем выбор ———
async def cb_consult(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await cmd_start(update, ctx)

# ——— CALLBACK: “Регистрация эксперта” — форму ———
async def cb_register_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    # вручную запускаем первый шаг регистрации
    await update.callback_query.message.reply_text("Введите ваше ФИО:")
    return REG_NAME

# ——— Регистрация эксперта — ConversationHandler ———
async def reg_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['fio'] = update.message.text
    await update.message.reply_text("Введите город:")
    return REG_CITY

async def reg_city(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['city'] = update.message.text
    await update.message.reply_text("Введите сферу деятельности:")
    return REG_FIELD

async def reg_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['sphere'] = update.message.text
    await update.message.reply_text("Напишите короткое описание:")
    return REG_DESC

async def reg_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['desc'] = update.message.text
    await update.message.reply_text("Прикрепите фото (сертификат и т.п.):")
    return REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    photo_id = update.message.photo[-1].file_id if update.message.photo else ""
    # сохраняем в Google Sheets
    experts_ws.append_row([
        ctx.user_data['fio'],
        ctx.user_data['city'],
        ctx.user_data['sphere'],
        ctx.user_data['desc'],
        photo_id,
        update.effective_user.id,
        update.effective_user.username or "",
        ""  # slots
    ])
    await update.message.reply_text("✅ Вы зарегистрированы как эксперт!")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Регистрация отменена.")
    return ConversationHandler.END

conv_reg = ConversationHandler(
    entry_points=[CallbackQueryHandler(cb_register_button, pattern="^register$")],
    states={
        REG_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
        REG_CITY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)],
        REG_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field)],
        REG_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_PHOTO: [MessageHandler(filters.PHOTO, reg_photo)],
    },
    fallbacks=[CommandHandler("cancel", reg_cancel)],
)

# ——— Добавление таймов эксперта (/time) — ConversationHandler ———
async def time_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    days = [(datetime.now()+timedelta(i)).strftime("%Y-%m-%d") for i in range(7)]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(d, callback_data=f"td_{d}")] for d in days])
    await update.message.reply_text("Выберите дату:", reply_markup=kb)
    ctx.user_data['slot_times'] = []
    return TIME_DATE

async def time_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    date = q.data.split("_",1)[1]
    ctx.user_data['slot_date'] = date
    hours = [f"{h:02d}:00" for h in range(8,21)]
    kb = []
    sel = set(ctx.user_data['slot_times'])
    for h in hours:
        prefix = "✅ " if h in sel else ""
        kb.append([InlineKeyboardButton(prefix+h, callback_data=f"tt_{h}")])
    kb.append([InlineKeyboardButton("Подтвердить", callback_data="tt_done")])
    await q.message.edit_text(f"Дата: {date}\nВыберите время:", reply_markup=InlineKeyboardMarkup(kb))
    return TIME_SELECT

async def time_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = q.data
    if data.startswith("tt_") and data!="tt_done":
        t = data.split("_",1)[1]
        lst = ctx.user_data['slot_times']
        lst = [x for x in lst if x!=t] if t in lst else lst+[t]
        ctx.user_data['slot_times'] = lst
        return await time_date(update, ctx)
    if data=="tt_done":
        date = ctx.user_data['slot_date']
        times = ctx.user_data['slot_times']
        ok = add_slots_for_specialist(q.from_user.id, date, times)
        text = "Успешно добавлено!" if ok else "Ошибка: сначала /register"
        await q.message.reply_text(text)
        return ConversationHandler.END
    return TIME_SELECT

conv_time = ConversationHandler(
    entry_points=[CommandHandler("time", time_start)],
    states={
        TIME_DATE:   [CallbackQueryHandler(time_date, pattern="^td_")],
        TIME_SELECT: [CallbackQueryHandler(time_select, pattern="^tt_")],
    },
    fallbacks=[]
)

# ——— Запись на консультацию — ConversationHandler ———
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # первый шаг: регион
    specs   = get_specialists()
    ctx.user_data['specs'] = specs
    regions = sorted({s['город эксперта'] for s in specs})
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(r, callback_data=f"rg_{r}")] for r in regions])
    if update.message:
        await update.message.reply_text("Выберите регион:", reply_markup=kb)
    else:
        await update.callback_query.message.reply_text("Выберите регион:", reply_markup=kb)
    return CH_REGION

async def cb_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q      = update.callback_query; await q.answer()
    region = q.data.split("_",1)[1]
    ctx.user_data['region'] = region
    # поля
    specs  = [s for s in ctx.user_data['specs'] if s['город эксперта']==region]
    fields = sorted({s['сфера'] for s in specs})
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f, callback_data=f"fl_{f}")] for f in fields] +
                             [[InlineKeyboardButton("← Назад", callback_data="back_start")]])
    await q.message.reply_text(f"Регион: {region}\nВыберите сферу:", reply_markup=kb)
    return CH_FIELD

async def cb_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q      = update.callback_query; await q.answer()
    field  = q.data.split("_",1)[1]
    region = ctx.user_data['region']
    specs  = [s for s in ctx.user_data['specs'] if s['город эксперта']==region and s['сфера']==field]
    ctx.user_data['field'] = field
    ctx.user_data['candidates'] = {str(s['Telegram ID']):s for s in specs}
    kb = InlineKeyboardMarkup(
         [[InlineKeyboardButton(s['ФИО эксперта'], callback_data=f"sp_{tid}")]
          for tid in ctx.user_data['candidates']] +
         [[InlineKeyboardButton("← Назад", callback_data="rg_"+region)]]
    )
    await q.message.reply_text(f"Сфера: {field}\nВыберите эксперта:", reply_markup=kb)
    return CH_SPEC

async def cb_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query; await q.answer()
    tid  = q.data.split("_",1)[1]
    spec = ctx.user_data['candidates'][tid]
    ctx.user_data['chosen_spec'] = spec
    # выводим описание + фото_id
    text = f"{spec['ФИО эксперта']}\n{spec['описание']}"
    if spec.get('photo_file_id'):
        await q.message.reply_photo(photo=spec['photo_file_id'], caption=text)
    else:
        await q.message.reply_text(text)
    # даты
    dates = sorted({slot.split()[0] for slot in spec['slots']})
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(d, callback_data=f"dt_{d}")] for d in dates] +
                             [[InlineKeyboardButton("← Назад", callback_data="fl_"+ctx.user_data['field'])]])
    await q.message.reply_text("Выберите дату:", reply_markup=kb)
    return CH_DATE

async def cb_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q      = update.callback_query; await q.answer()
    date   = q.data.split("_",1)[1]
    spec   = ctx.user_data['chosen_spec']
    slots  = [s for s in spec['slots'] if s.startswith(date)]
    times  = [s.split()[1] for s in slots]
    ctx.user_data['book_date'] = date
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=f"tm_{t}")] for t in times] +
                             [[InlineKeyboardButton("← Назад", callback_data="sp_"+str(spec['Telegram ID']))]])
    await q.message.reply_text("Выберите время:", reply_markup=kb)
    return CH_TIME

async def cb_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q      = update.callback_query; await q.answer()
    t      = q.data.split("_",1)[1]
    date   = ctx.user_data['book_date']
    spec   = ctx.user_data['chosen_spec']
    fio    = ctx.user_data.get('user_fio',"") or update.effective_user.full_name
    # сохраняем в Гугл
    bookings_ws.append_row([fio, spec['ФИО эксперта'], date, t])
    await q.message.reply_text(f"✅ Вы записаны к {spec['ФИО эксперта']} на {date} {t}")
    return ConversationHandler.END

async def back_to_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await start_menu(update, ctx)

conv_consult = ConversationHandler(
    entry_points=[CallbackQueryHandler(cb_consult, pattern="^consult$")],
    states={
        CH_REGION: [CallbackQueryHandler(cb_region, pattern="^rg_")],
        CH_FIELD:  [CallbackQueryHandler(cb_field, pattern="^fl_|^back_start$")],
        CH_SPEC:   [CallbackQueryHandler(cb_spec, pattern="^sp_")],
        CH_DATE:   [CallbackQueryHandler(cb_date, pattern="^dt_")],
        CH_TIME:   [CallbackQueryHandler(cb_time, pattern="^tm_")],
    },
    fallbacks=[CallbackQueryHandler(back_to_start, pattern="^back_start$")]
)

# ——— Сборка приложения ———
application = ApplicationBuilder().token(TOKEN).build()

application.add_handler(CommandHandler("start", start_menu))
application.add_handler(conv_reg)
application.add_handler(conv_time)
application.add_handler(conv_consult)

# ——— Запуск совместно с Flask (для Render) ———
def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
