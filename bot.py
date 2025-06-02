import os
import logging
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ========== КОНФИГУРАЦИЯ ==========

TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

if not TOKEN or not ADMIN_CHAT_ID:
    logging.error("TELEGRAM_TOKEN и/или ADMIN_CHAT_ID не заданы")
    exit(1)

ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)
PORT = int(os.getenv("PORT", "5000"))  # Render назначает свой PORT
# ====================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Списки регионов/отраслей/специалистов, как раньше
REGIONS = ["Москва", "Санкт-Петербург", "Краснодарский край"]
INDUSTRIES = ["Психология", "Финансы", "Юриспруденция"]
SPECIALISTS = [
    {
        "id": "spec1",
        "name": "Анна Иванова",
        "description": "Психолог-консультант, опыт 5 лет",
        "contact": "@anna_ivanova_bot",
        "region": "Москва",
        "industry": "Психология",
    },
    {
        "id": "spec2",
        "name": "Игорь Петров",
        "description": "Финансовый консультант, поможет с бюджетом",
        "contact": "@igor_petrov",
        "region": "Москва",
        "industry": "Финансы",
    },
    {
        "id": "spec3",
        "name": "Мария Сидорова",
        "description": "Юрист по недвижимости",
        "contact": "@maria_sidorova",
        "region": "Санкт-Петербург",
        "industry": "Юриспруденция",
    },
    {
        "id": "spec4",
        "name": "Елена Кузнецова",
        "description": "Финансовый аналитик, опыт работы в банках",
        "contact": "@elena_kuznetsova",
        "region": "Краснодарский край",
        "industry": "Финансы",
    },
]

CHOOSING_REGION, CHOOSING_INDUSTRY, CHOOSING_SPECIALIST, TYPING_REQUEST = range(4)

# ======== ЛОГИКА TELEGRAM-БОТА ========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [InlineKeyboardButton(text=region, callback_data=region)]
        for region in REGIONS
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Привет! Выберите ваш регион:", reply_markup=reply_markup
    )
    return CHOOSING_REGION


async def handle_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    region = query.data
    context.user_data["region"] = region

    keyboard = [
        [InlineKeyboardButton(text=industry, callback_data=industry)]
        for industry in INDUSTRIES
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        f"Регион: {region}\n\nТеперь выберите отрасль:", reply_markup=reply_markup
    )
    return CHOOSING_INDUSTRY


async def handle_industry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    industry = query.data
    context.user_data["industry"] = industry

    region = context.user_data["region"]
    filtered = [
        s for s in SPECIALISTS if s["region"] == region and s["industry"] == industry
    ]
    if not filtered:
        await query.edit_message_text(
            f"По региону «{region}» и отрасли «{industry}» специалисты не найдены."
        )
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(text=s["name"], callback_data=s["id"])]
        for s in filtered
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        f"Регион: {region}\nОтрасль: {industry}\n\nВыберите специалиста:",
        reply_markup=reply_markup,
    )
    return CHOOSING_SPECIALIST


async def handle_specialist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    spec_id = query.data
    spec = next((s for s in SPECIALISTS if s["id"] == spec_id), None)
    if not spec:
        await query.edit_message_text("Специалист не найден.")
        return ConversationHandler.END

    context.user_data["specialist"] = spec
    await query.edit_message_text(
        f"Вы выбрали: {spec['name']}\n"
        f"{spec['description']}\n\n"
        f"Напишите текст вашей заявки, и я перешлю её."
    )
    return TYPING_REQUEST


async def handle_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    user = update.message.from_user
    region = context.user_data.get("region")
    industry = context.user_data.get("industry")
    spec = context.user_data.get("specialist")

    message_to_admin = (
        f"Новая заявка:\n\n"
        f"Пользователь: {user.full_name} (id={user.id})\n"
        f"Регион: {region}\n"
        f"Отрасль: {industry}\n"
        f"Специалист: {spec['name']} ({spec['contact']})\n\n"
        f"Текст заявки:\n{text}"
    )
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=message_to_admin)
    await update.message.reply_text("Ваша заявка отправлена. Спасибо.")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END


def run_bot():
    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_REGION: [CallbackQueryHandler(handle_region)],
            CHOOSING_INDUSTRY: [CallbackQueryHandler(handle_industry)],
            CHOOSING_SPECIALIST: [CallbackQueryHandler(handle_specialist)],
            TYPING_REQUEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_request)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv_handler)

    app.run_polling()


# ======= НЕБОЛЬШОЙ HTTP-СЕРВЕР ДЛЯ RENDER =======

flask_app = Flask("")

@flask_app.route("/")
def health_check():
    return "OK", 200


def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    # запускаем бот в отдельном потоке
    threading.Thread(target=run_bot, daemon=True).start()
    # а здесь запускаем Flask, он не даст Render считать порт закрытым
    run_flask()
