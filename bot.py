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
    raise ValueError("❌ Переменные окружения TELEGRAM_TOKEN и WEBHOOK_URL должны быть заданы!")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

# -------------------------
# Логирование
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# -------------------------
# Загрузка советов из файла
# -------------------------
try:
    with open("phrases.txt", "r", encoding="utf-8") as f:
        content = f.read()
    phrases = [p.strip() for p in content.split('---') if p.strip()]
    if not phrases:
        phrases = ["Файл пуст! Добавь советы через ---"]
except FileNotFoundError:
    phrases = ["Файл с советами не найден! Добавь phrases.txt в проект."]

logging.info(f"Загружено {len(phrases)} советов")

# -------------------------
# Хранилище советов
# -------------------------
daily_phrase = {}
last_phrase = {}

def get_daily_phrase(chat_id: int) -> str:
    today = str(date.today())
    if daily_phrase.get(chat_id, {}).get("date") != today:
        phrase = random.choice(phrases)
        daily_phrase[chat_id] = {"date": today, "phrase": phrase}
    return daily_phrase[chat_id]["phrase"]

def get_random_phrase(chat_id: int) -> str:
    available = [p for p in phrases if p != last_phrase.get(chat_id)]
    phrase = random.choice(available) if available else random.choice(phrases)
    last_phrase[chat_id] = phrase
    return phrase

def get_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(text="📅 Совет дня", callback_data="daily"),
        types.InlineKeyboardButton(text="💡 Новый совет", callback_data="random"),
    )
    return keyboard

# -------------------------
# Хэндлер /start
# -------------------------
@bot.message_handler(commands=["start"])
def start_msg(message):
    logging.info(f"/start от пользователя {message.chat.id}")
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass
    bot.send_message(
        message.chat.id,
        "Привет! Я бот советов на каждый день 🌞\n\nВыбери, что хочешь получить:",
        reply_markup=get_keyboard(),
    )

# -------------------------
# Хэндлер inline-кнопок
# -------------------------
@bot.callback_query_handler(func=lambda c: True)
def callback_inline(c):
    if c.data == "daily":
        phrase = get_daily_phrase(c.message.chat.id)
        today_str = datetime.now().strftime("%d.%m.%Y")
        text = f"📅 <b>Совет на сегодня ({today_str}):</b>\n\n{phrase}"
        bot.answer_callback_query(c.id, "Сегодняшний совет уже выдан ✅", show_alert=False)
    elif c.data == "random":
        phrase = get_random_phrase(c.message.chat.id)
        text = f"💡 <b>Совет:</b>\n\n{phrase}"
        bot.answer_callback_query(c.id, "Получен новый совет 🌟", show_alert=False)
    else:
        return

    kb = get_keyboard()
    try:
        bot.edit_message_text(
            chat_id=c.message.chat.id,
            message_id=c.message.message_id,
            text=text,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    except Exception:
        bot.send_message(c.message.chat.id, text, reply_markup=kb)

    logging.info(f"Пользователь {c.message.chat.id} получил совет: {phrase}")

# -------------------------
# Flask эндпоинты
# -------------------------
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    json_str = request.get_data().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "ok", 200

@app.route("/", methods=["GET"])
def index():
    return "Бот работает!", 200

# -------------------------
# Устанавливаем вебхук при запуске
# -------------------------
bot.remove_webhook()
bot.set_webhook(url=f"{APP_URL}/{TOKEN}")
logging.info(f"✅ Webhook установлен: {APP_URL}/{TOKEN}")
