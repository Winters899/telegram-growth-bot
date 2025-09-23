import os
import telebot
from flask import Flask, request
from telebot import types
import random
import logging
from datetime import date

# -------------------------
# Настройки
# -------------------------
TOKEN = os.environ["TELEGRAM_TOKEN"]
APP_URL = os.environ["WEBHOOK_URL"]

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

# -------------------------
# Логирование
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

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

# -------------------------
# Хранилище советов дня
# -------------------------
daily_phrase = {}
last_phrase = {}

def get_daily_phrase(chat_id):
    """Фиксированный совет дня для конкретного чата"""
    today = str(date.today())
    if daily_phrase.get(chat_id, {}).get("date") != today:
        phrase = random.choice(phrases)
        daily_phrase[chat_id] = {"date": today, "phrase": phrase}
    return daily_phrase[chat_id]["phrase"]

def get_random_phrase(chat_id):
    """Дополнительный совет без повторов"""
    available = [p for p in phrases if p != last_phrase.get(chat_id)]
    phrase = random.choice(available) if available else random.choice(phrases)
    last_phrase[chat_id] = phrase
    return phrase

# -------------------------
# Хэндлер команды /start
# -------------------------
@bot.message_handler(commands=['start'])
def start_msg(message):
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except:
        pass  

    keyboard = types.InlineKeyboardMarkup()
    day_button = types.InlineKeyboardButton(text="📅 Совет дня", callback_data="daily")
    again_button = types.InlineKeyboardButton(text="💡 Новый совет", callback_data="random")
    keyboard.add(day_button, again_button)
    
    bot.send_message(
        message.chat.id,
        "Привет! Я бот советов на каждый день 🌞\n\nВыбери, что хочешь получить:",
        reply_markup=keyboard
    )

# -------------------------
# Хэндлер нажатий на inline-кнопки
# -------------------------
@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    if call.data == "daily":
        bot.answer_callback_query(call.id)
        phrase = get_daily_phrase(call.message.chat.id)
        text = f"📅 <b>Совет на сегодня:</b>\n\n{phrase}"

    elif call.data == "random":
        bot.answer_callback_query(call.id)
        phrase = get_random_phrase(call.message.chat.id)
        text = f"💡 <b>Дополнительный совет:</b>\n\n{phrase}"

    else:
        return

    keyboard = types.InlineKeyboardMarkup()
    day_button = types.InlineKeyboardButton(text="📅 Совет дня", callback_data="daily")
    again_button = types.InlineKeyboardButton(text="💡 Новый совет", callback_data="random")
    keyboard.add(day_button, again_button)

    try:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=text,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=keyboard)

    logging.info(f"User {call.message.chat.id} получил совет: {phrase}")

# -------------------------
# Route для webhook
# -------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    json_str = request.get_data(as_text=True)
    if not json_str:
        return "empty", 200
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "ok", 200

# -------------------------
# Ручная установка вебхука
# -------------------------
@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    bot.remove_webhook()
    success = bot.set_webhook(url=f"{APP_URL}/webhook")
    return f"Webhook set: {success}", 200

# -------------------------
# Запуск Flask
# -------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
