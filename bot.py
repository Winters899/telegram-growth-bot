import os
import random
import logging
from datetime import date, datetime

import telebot
from telebot import types
from flask import Flask, request

# -------------------------
# Настройки
# -------------------------
TOKEN = os.environ.get("TELEGRAM_TOKEN")
APP_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")

if not TOKEN or not APP_URL:
    raise ValueError("❌ TELEGRAM_TOKEN и WEBHOOK_URL должны быть заданы!")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

# -------------------------
# Логирование
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# -------------------------
# Загрузка советов
# -------------------------
try:
    with open("phrases.txt", "r", encoding="utf-8") as f:
        phrases = [p.strip() for p in f.read().split('---') if p.strip()]
except FileNotFoundError:
    phrases = []

if not phrases:
    phrases = ["Советы не найдены. Добавь файл phrases.txt с советами через '---'"]

logging.info(f"Загружено {len(phrases)} советов")

# -------------------------
# Хранилище
# -------------------------
daily_phrase = {}
last_phrase = {}

# -------------------------
# Функции
# -------------------------
def get_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("📅 Совет дня", callback_data="daily"),
        types.InlineKeyboardButton("💡 Новый совет", callback_data="random"),
    )
    return kb


def get_daily_phrase(chat_id: int) -> str:
    today = str(date.today())
    if daily_phrase.get(chat_id, {}).get("date") != today:
        daily_phrase[chat_id] = {"date": today, "phrase": random.choice(phrases)}
    return daily_phrase[chat_id]["phrase"]


def get_random_phrase(chat_id: int) -> str:
    available = [p for p in phrases if p != last_phrase.get(chat_id)]
    phrase = random.choice(available or phrases)
    last_phrase[chat_id] = phrase
    return phrase


def decorate_phrase(phrase: str) -> str:
    # Список эмодзи
    emojis = ["✨", "⭐", "🌟", "💎", "🔥", "💡", "🌱", "📌", "🔑", "🚀"]
    emoji = random.choice(emojis)
    return f"{phrase} {emoji}"


def send_or_edit(c, new_text: str):
    kb = get_keyboard()
    old_text = c.message.text or ""

    # Если текст и клавиатура не изменились — не редактируем
    if new_text.strip() == old_text.strip() and c.message.reply_markup == kb:
        logging.debug("Редактирование пропущено: текст и клавиатура совпадают")
        return

    try:
        bot.edit_message_text(
            chat_id=c.message.chat.id,
            message_id=c.message.message_id,
            text=new_text,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logging.warning(f"Не удалось отредактировать сообщение: {e}")

# -------------------------
# Хэндлеры
# -------------------------
@bot.message_handler(commands=["start"])
def start_msg(message):
    logging.info(f"/start от {message.chat.id}")
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass
    bot.send_message(
        message.chat.id,
        "Привет! Я бот советов 🌞\n\nВыбери опцию:",
        reply_markup=get_keyboard(),
    )


@bot.callback_query_handler(func=lambda c: True)
def callback_inline(c):
    if c.data == "daily":
        phrase = get_daily_phrase(c.message.chat.id)
        phrase = decorate_phrase(phrase)
        today = datetime.now().strftime("%d.%m.%Y")
        text = f"🗓💡 <b>Совет на сегодня ({today}):</b>\n\n{phrase}"
        bot.answer_callback_query(c.id, "Сегодняшний совет ✅", show_alert=False)

    elif c.data == "random":
        phrase = get_random_phrase(c.message.chat.id)
        phrase = decorate_phrase(phrase)
        # Заголовки для "нового совета" — случайный вдохновляющий эмодзи
        headers = ["✨", "🌟", "🔥", "🚀", "⭐", "💎"]
        header = random.choice(headers)
        text = f"{header} <b>Новый совет:</b>\n\n{phrase}"
        bot.answer_callback_query(c.id, "Новый совет 🌟", show_alert=False)

    else:
        return

    send_or_edit(c, text)
    logging.info(f"Пользователь {c.message.chat.id} получил совет: {phrase}")

# -------------------------
# Flask эндпоинты
# -------------------------
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.data.decode("utf-8"))
    bot.process_new_updates([update])
    return "ok", 200


@app.route("/", methods=["GET"])
def index():
    return "Бот работает!", 200

# -------------------------
# Запуск приложения
# -------------------------
if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=f"{APP_URL}/{TOKEN}")
    logging.info(f"✅ Webhook установлен: {APP_URL}/{TOKEN}")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
