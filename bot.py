import os
import json
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Переменные окружения
TOKEN      = os.environ['TELEGRAM_TOKEN']
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = json.loads(os.environ['GSPREAD_CREDENTIALS_JSON'])
PORT       = int(os.environ.get('PORT', '8443'))

# Подключение к Google Sheets
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_dict(CREDS_JSON, SCOPES)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID)
ws      = sheet.worksheet('Лист1')            # Таблица экспертов
book_ws = sheet.add_worksheet(title="Заявки", rows="1000", cols="5") \
               if 'Заявки' not in [s.title for s in sheet.worksheets()] \
               else sheet.worksheet('Заявки')

# Flask для healthcheck и вебхука
app = Flask(__name__)

@app.route('/', methods=['GET', 'HEAD'])
def health():
    return 'OK', 200

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    application.process_update(update)
    return '', 200

# ConversationHandler states
(
    C_START,
    C_REGION, C_FIELD, C_SPEC,
    C_DATE, C_TIME, C_CONFIRM
) = range(7)

# --- /start ---
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📋 Консультации", callback_data="do_consult")],
        [InlineKeyboardButton("✏️ Регистрация эксперта", callback_data="do_register")]
    ]
    await update.message.reply_text("Выберите действие:", reply_markup=InlineKeyboardMarkup(kb))
    return C_START

# --- Регистрация нового эксперта ---
async def reg_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Введите ваше ФИО:")
    return C_REGION

async def reg_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['reg_fio'] = update.message.text
    await update.message.reply_text("Введите город эксперта:")
    return C_FIELD

async def reg_city(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['reg_city'] = update.message.text
    await update.message.reply_text("Введите сферу экспертизы:")
    return C_SPEC

async def reg_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['reg_sphere'] = update.message.text
    await update.message.reply_text("Кратко опишите себя:")
    return C_DATE

async def reg_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['reg_desc'] = update.message.text
    await update.message.reply_text("Пришлите ваше фото (сертификат или портрет):")
    return C_TIME

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    file_id = update.message.photo[-1].file_id if update.message.photo else ''
    ws.append_row([
        datetime.now().isoformat(),
        ctx.user_data['reg_fio'],
        ctx.user_data['reg_city'],
        ctx.user_data['reg_sphere'],
        ctx.user_data['reg_desc'],
        file_id,
        update.effective_user.id,
        update.effective_user.username or '',
        ""  # Slots
    ])
    await update.message.reply_text("✅ Вы зарегистрированы как эксперт!")
    return ConversationHandler.END

# --- Запись на консультацию ---
async def consult_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    records = ws.get_all_records()
    regions = sorted({r['город эксперта'] for r in records})
    kb = [[InlineKeyboardButton(r, callback_data=f"region|{r}")] for r in regions]
    kb += [[InlineKeyboardButton("Назад", callback_data="back_to_menu")]]
    await update.callback_query.message.reply_text("Выберите город:", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['records'] = records
    return C_REGION

async def consult_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, region = update.callback_query.data.split("|",1)
    ctx.user_data['region'] = region
    recs = [r for r in ctx.user_data['records'] if r['город эксперта']==region]
    spheres = sorted({r['сфера'] for r in recs})
    kb = [[InlineKeyboardButton(s, callback_data=f"field|{s}")] for s in spheres]
    kb += [[InlineKeyboardButton("Назад", callback_data="do_consult")]]
    await update.callback_query.message.reply_text(f"Город: {region}\nВыберите сферу:", reply_markup=InlineKeyboardMarkup(kb))
    return C_FIELD

async def consult_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, sphere = update.callback_query.data.split("|",1)
    ctx.user_data['sphere'] = sphere
    recs = [r for r in ctx.user_data['records']
            if r['город эксперта']==ctx.user_data['region']
           and r['сфера']==sphere]
    kb = [[InlineKeyboardButton(r['ФИО эксперта'], callback_data=f"spec|{r['Telegram ID']}")] for r in recs]
    kb += [[InlineKeyboardButton("Назад", callback_data="region|"+ctx.user_data['region'])]]
    await update.callback_query.message.reply_text(f"Сфера: {sphere}\nВыберите эксперта:", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['filtered'] = {str(r['Telegram ID']): r for r in recs}
    return C_SPEC

async def consult_spec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, tid = update.callback_query.data.split("|",1)
    spec = ctx.user_data['filtered'][tid]
    ctx.user_data['spec'] = spec
    text = f"{spec['ФИО эксперта']}\n{spec['описание']}"
    if spec.get('photo_file_id'):
        await update.callback_query.message.reply_photo(photo=spec['photo_file_id'], caption=text)
    else:
        await update.callback_query.message.reply_text(text)
    # даты из Slots
    slots = (spec.get('Slots') or "").split(";")
    dates = sorted({s.split()[0] for s in slots if s})
    kb = [[InlineKeyboardButton(d, callback_data=f"date|{d}")] for d in dates]
    kb += [[InlineKeyboardButton("Назад", callback_data=f"field|{ctx.user_data['sphere']}")]]
    await update.callback_query.message.reply_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(kb))
    return C_DATE

async def consult_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, date = update.callback_query.data.split("|",1)
    ctx.user_data['date'] = date
    spec = ctx.user_data['spec']
    slots = [s for s in spec['Slots'].split(";") if s.startswith(date)]
    times = [s.split()[1] for s in slots]
    kb = [[InlineKeyboardButton(t, callback_data=f"time|{t}")] for t in times]
    # кнопка «Подтвердить»
    kb += [[InlineKeyboardButton("Подтвердить", callback_data="confirm")]]
    kb += [[InlineKeyboardButton("Назад", callback_data=f"spec|{spec['Telegram ID']}")]]
    await update.callback_query.message.reply_text("Выберите время (можно несколько):",
                                                 reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['sel_times'] = []
    return C_TIME

async def consult_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    if data.startswith("time|"):
        t = data.split("|",1)[1]
        sel = ctx.user_data['sel_times']
        if t in sel: sel.remove(t)
        else:        sel.append(t)
        ctx.user_data['sel_times'] = sel
        # просто перерисуем кнопки галочками
        buttons = []
        for slot in ctx.user_data['sel_times']:
            buttons.append(f"✅ {slot}")
        await update.callback_query.message.edit_reply_markup(
            InlineKeyboardMarkup(
                [[InlineKeyboardButton(
                    ("✅ " if slot in sel else "")+slot,
                    callback_data=f"time|{slot}"
                )] for slot in sorted(set(sel+sel))]  # просто фикс, чтобы не ломалось
                +[[InlineKeyboardButton("Подтвердить", callback_data="confirm")]]
            )
        )
        return C_TIME

    elif data == "confirm":
        spec = ctx.user_data['spec']
        fio  = update.effective_user.full_name
        date = ctx.user_data['date']
        times = ctx.user_data['sel_times']
        # Запишем в Google Sheets
        book_ws.append_row([datetime.now().isoformat(), fio,
                            spec['ФИО эксперта'], date, ", ".join(times)])
        await update.callback_query.message.reply_text(
            f"✅ Запись: {spec['ФИО эксперта']} на {date} {', '.join(times)}"
        )
        return ConversationHandler.END

# --- Сброс к меню ---
async def go_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await cmd_start(update, ctx)

# --- Сборка приложения ---
application = ApplicationBuilder().token(TOKEN).build()

reg_conv = ConversationHandler(
    entry_points=[ CallbackQueryHandler(reg_start, pattern="^do_register$") ],
    states={
        C_REGION: [ MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name) ],
        C_FIELD:  [ MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city) ],
        C_SPEC:   [ MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field) ],
        C_DATE:   [ MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc) ],
        C_TIME:   [ MessageHandler(filters.PHOTO,               reg_photo) ],
    },
    fallbacks=[ CallbackQueryHandler(go_back, pattern="^back_to_menu$") ],
    per_message=False
)

consult_conv = ConversationHandler(
    entry_points=[ CallbackQueryHandler(consult_start,   pattern="^do_consult$") ],
    states={
        C_REGION:  [ CallbackQueryHandler(consult_region, pattern="^region\\|") ],
        C_FIELD:   [ CallbackQueryHandler(consult_field,  pattern="^field\\|") ],
        C_SPEC:    [ CallbackQueryHandler(consult_spec,   pattern="^spec\\|") ],
        C_DATE:    [ CallbackQueryHandler(consult_date,   pattern="^date\\|") ],
        C_TIME:    [
            CallbackQueryHandler(consult_time, pattern="^time\\|"),
            CallbackQueryHandler(consult_time, pattern="^confirm$")
        ],
    },
    fallbacks=[ CallbackQueryHandler(go_back, pattern="^back_to_menu$") ],
    per_message=False
)

application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(reg_conv)
application.add_handler(consult_conv)

# И вебхук + polling
def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
