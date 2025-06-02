import os
import logging
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

# Логирование (по желанию)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# Чат ID администратора (куда будут уходить заявки)
ADMIN_CHAT_ID = 123456789  # замените на свой ID или перенесите в ENV

# Списки регионов и отраслей
REGIONS = ["Москва", "Санкт-Петербург", "Краснодарский край"]
INDUSTRIES = ["Психология", "Финансы", "Юриспруденция"]

# Список специалистов с их параметрами
SPECIALISTS = [
    {
        "id": "spec1",
        "name": "Анна Иванова",
        "description": "Психолог-консультант, опыт 5 лет",
        "contact": "@anna_ivanova_bot",
        "region": "Москва",
        "industry": "Психология"
    },
    {
        "id": "spec2",
        "name": "Игорь Петров",
        "description": "Финансовый консультант, поможет с бюджетом",
        "contact": "@igor_petrov",
        "region": "Москва",
        "industry": "Финансы"
    },
    {
        "id": "spec3",
        "name": "Мария Сидорова",
        "description": "Юрист по недвижимости",
        "contact": "@maria_sidorova",
        "region": "Санкт-Петербург",
        "industry": "Юриспруденция"
    },
    {
        "id": "spec4",
        "name": "Елена Кузнецова",
        "description": "Финансовый аналитик, опыт работы в банках",
        "contact": "@elena_kuznetsova",
        "region": "Краснодарский край",
        "industry": "Финансы"
    },
    # Добавьте своих специалистов при необходимости
]

# Состояния ConversationHandler
CHOOSING_REGION, CHOOSING_INDUSTRY, CHOOSING_SPECIALIST, TYPING_REQUEST = range(4)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /start: показываем список регионов."""
    keyboard = [
        [InlineKeyboardButton(text=region, callback_data=region)]
        for region in REGIONS
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Привет! Выберите ваш регион:",
        reply_markup=reply_markup
    )
    return CHOOSING_REGION

async def handle_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь выбрал регион."""
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
        f"Регион: {region}\n\nТеперь выберите отрасль:",
        reply_markup=reply_markup
    )
    return CHOOSING_INDUSTRY

async def handle_industry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь выбрал отрасль."""
    query = update.callback_query
    await query.answer()
    industry = query.data
    context.user_data["industry"] = industry

    region = context.user_data["region"]
    filtered = [
        spec for spec in SPECIALISTS
        if spec["region"] == region and spec["industry"] == industry
    ]

    if not filtered:
        await query.edit_message_text(
            f"По региону «{region}» и отрасли «{industry}» специалисты не найдены."
        )
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(text=spec["name"], callback_data=spec["id"])]
        for spec in filtered
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        f"Регион: {region}\nОтрасль: {industry}\n\nВыберите специалиста:",
        reply_markup=reply_markup
    )
    return CHOOSING_SPECIALIST

async def handle_specialist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь выбрал специалиста."""
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
    """Получаем текст заявки и пересылаем админу."""
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
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=message_to_admin
    )
    await update.message.reply_text("Ваша заявка отправлена. Спасибо.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /cancel: отмена."""
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END

def main() -> None:
    # Получаем токен из переменной окружения TELEGRAM_TOKEN
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        logging.error("Переменная окружения TELEGRAM_TOKEN не задана")
        return

    app = ApplicationBuilder().token(token).build()

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

if __name__ == "__main__":
    main()
