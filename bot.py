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
TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
APP_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")

if not TOKEN:
    raise RuntimeError("❌ Не задан TELEGRAM_TOKEN")

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

def send_or_edit(c, text: str):
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
        today = datetime.now().strftime("%d.%m.%Y")
        text = f"📅 <b>Совет на сегодня ({today}):</b>\n\n{phrase}"
        bot.answer_callback_query(c.id, "Сегодняшний совет ✅", show_alert=False)
    elif c.data == "random":
        phrase = get_random_phrase(c.message.chat.id)
        text = f"💡 <b>Совет:</b>\n\n{phrase}"
        bot.answer_callback_query(c.id, "Новый совет 🌟", show_alert=False)
    else:
        return
    send_or_edit(c, text)
    logging.info(f"Пользователь {c.message.chat.id} получил совет: {phrase}")

# -------------------------
# Flask эндпоинты (для webhook)
# -------------------------
@app.route(f"/webhook/{TOKEN}", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.data.decode("utf-8"))
    bot.process_new_updates([update])
    return "ok", 200

@app.route("/", methods=["GET"])
def index():
    return "Бот работает!", 200

# -------------------------
# Запуск
# -------------------------
if __name__ == "__main__":
    if APP_URL:  # режим webhook (сервер)
        bot.remove_webhook()
        bot.set_webhook(url=f"{APP_URL}/webhook/{TOKEN}")
        logging.info(f"✅ Webhook установлен: {APP_URL}/webhook/{TOKEN}")
        port = int(os.environ.get("PORT", 5000))
        app.run(host="0.0.0.0", port=port, debug=False)
    else:  # режим polling (локально)
        logging.info("▶ Запуск в режиме polling (локально)")
        bot.remove_webhook()
        bot.infinity_polling(skip_pending=True)
