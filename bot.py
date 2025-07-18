import os
import json
import logging
import gspread
from google.oauth2.service_account import Credentials
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# ——— Логирование —————————————————————————————————————————————————
logging.basicConfig(level=logging.INFO)

# ——— Переменные окружения —————————————————————————————————————
TOKEN      = os.environ['TELEGRAM_TOKEN']
SHEET_ID   = os.environ['SHEET_ID']
CREDS_JSON = os.environ['GSPREAD_CREDENTIALS_JSON']
PORT       = int(os.environ.get('PORT', '8080'))

# ——— Google Sheets подключение ————————————————————————————————————
creds_dict = json.loads(CREDS_JSON)
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SHEET_ID)

# ——— Flask healthcheck (Render) ———————————————————————————
app = Flask(__name__)

@app.route('/', methods=['GET','HEAD'])
def health():
    return 'OK', 200

# ——— ConversationHandler: регистрация эксперта ————————————————————
REG_NAME, REG_CITY, REG_FIELD, REG_DESC, REG_PHOTO = range(5)

async def reg_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # может прийти и как callback_query
    if update.callback_query:
        await update.callback_query.answer()
        msg = update.callback_query.message
    else:
        msg = update.message
    await msg.reply_text("Введите ваше ФИО:")
    return REG_NAME

async def reg_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['fio'] = update.message.text
    await update.message.reply_text("Введите город:")
    return REG_CITY

async def reg_city(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['city'] = update.message.text
    await update.message.reply_text("Введите сферу деятельности:")
    return REG_FIELD

async def reg_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['field'] = update.message.text
    await update.message.reply_text("Кратко о себе:")
    return REG_DESC

async def reg_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['desc'] = update.message.text
    await update.message.reply_text("Пришлите фото (сертификат или документ):")
    return REG_PHOTO

async def reg_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # сохраняем file_id
    ctx.user_data['photo_file_id'] = (
        update.message.photo[-1].file_id
        if update.message.photo else
        ''
    )
    # добавляем строку в Google-лист
    ws = spreadsheet.worksheet('Лист1')
    ws.append_row([
        datetime.now().isoformat(),
        ctx.user_data['fio'],
        ctx.user_data['city'],
        ctx.user_data['field'],
        ctx.user_data['desc'],
        ctx.user_data['photo_file_id'],
        update.effective_user.id,
        update.effective_user.username or '',
        ""
    ])
    await update.message.reply_text("Готово! Вы зарегистрированы как эксперт.")
    return ConversationHandler.END

async def reg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Регистрация отменена.")
    return ConversationHandler.END

conv_reg = ConversationHandler(
    entry_points=[
        CommandHandler("register", reg_start),
        CallbackQueryHandler(reg_start, pattern="^register$")
    ],
    states={
        REG_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
        REG_CITY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_city)],
        REG_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_field)],
        REG_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_desc)],
        REG_PHOTO: [MessageHandler(filters.PHOTO, reg_photo)],
    },
    fallbacks=[CommandHandler("cancel", reg_cancel)],
    per_message=False
)

# ——— ConversationHandler: добавление слотов ——————————————————————
TIME_DATE, TIME_SELECT = range(2)

def get_specialist_row(telegram_id):
    ws = spreadsheet.worksheet('Лист1')
    for i, row in enumerate(ws.get_all_records(), start=2):
        if str(row.get('Telegram ID')) == str(telegram_id):
            return ws, i
    return None, None

async def time_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    dates = [(datetime.now() + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
    kb = [[InlineKeyboardButton(d, callback_data=f'timedate_{d}')] for d in dates]
    await update.message.reply_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data['slot_times'] = []
    return TIME_DATE

async def time_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    date = q.data.split('_',1)[1]
    ctx.user_data['slot_date'] = date
    times = [f"{h:02d}:00" for h in range(8,21)]
    sel = set(ctx.user_data.get('slot_times',[]))
    kb = [
        [InlineKeyboardButton((('✅ ' if t in sel else '') + t), callback_data=f'timeselect_{t}')]
        for t in times
    ] + [
        [InlineKeyboardButton("Готово", callback_data='timeconfirm')],
        [InlineKeyboardButton("Отмена", callback_data='timeback')]
    ]
    await q.message.edit_text(f"Дата: {date}\nВыберите время:", reply_markup=InlineKeyboardMarkup(kb))
    return TIME_SELECT

async def time_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = q.data
    if data.startswith('timeselect_'):
        t = data.split('_',1)[1]
        lst = ctx.user_data.setdefault('slot_times',[])
        if t in lst: lst.remove(t)
        else:         lst.append(t)
        return await time_date(update, ctx)
    if data == 'timeconfirm':
        ws, row = get_specialist_row(update.effective_user.id)
        if not row:
            await q.message.reply_text("Сначала зарегистрируйтесь (/register).")
        else:
            date = ctx.user_data['slot_date']
            slots = set((ws.cell(row,8).value or '').split(';'))
            for t in ctx.user_data['slot_times']:
                slots.add(f"{date} {t}")
            ws.update_cell(row,8,';'.join(sorted(slots)))
            await q.message.reply_text(f"Слоты {date}: {', '.join(ctx.user_data['slot_times'])} добавлены.")
        return ConversationHandler.END
    return ConversationHandler.END

conv_time = ConversationHandler(
    entry_points=[CommandHandler("time", time_start)],
    states={
        TIME_DATE:  [CallbackQueryHandler(time_date,  pattern=r'^timedate_')],
        TIME_SELECT:[CallbackQueryHandler(time_select, pattern=r'^(timeselect_|timeconfirm|timeback)')]
    },
    fallbacks=[]
)

# ——— Основной /start и кнопки ——————————————————————————————————
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📋 Консультации", callback_data="consult")],
        [InlineKeyboardButton("🖋 Регистрация эксперта", callback_data="register")]
    ]
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("Выберите действие:", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text("Выберите действие:", reply_markup=InlineKeyboardMarkup(kb))

application = ApplicationBuilder().token(TOKEN).build()

application.add_handler(conv_reg)
application.add_handler(conv_time)
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(CallbackQueryHandler(cmd_start, pattern="^consult$"))
application.add_handler(CallbackQueryHandler(reg_start, pattern="^register$"))

# ——— Запускаем одновременно Flask (health) и polling бот —————————
def run_flask():
    app.run(host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    application.run_polling()
