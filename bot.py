import os
import telebot
from telebot import types
import random
import logging
from datetime import date

# -------------------------
# Настройки
# -------------------------
TOKEN = os.environ["TELEGRAM_TOKEN"]

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
bot.remove_webhook()  # Удаляем вебхук для поллинга

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

logging.info(f"Загружено {len(phrases)} советов")

# -------------------------
# Хранилище советов
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

def get_keyboard():
    """Единая клавиатура для всех сообщений"""
    keyboard = types.InlineKeyboardMarkup()
    day_button = types.InlineKeyboardButton(text="📅 Совет дня", callback_data="daily")
    again_button = types.InlineKeyboardButton(text="💡 Новый совет", callback_data="random")
    keyboard.add(day_button, again_button)
    return keyboard

# -------------------------
# Хэндлер команды /start
# -------------------------
@bot.message_handler(commands=['start'])
def start_msg(message):
    logging.info(f"Received /start from chat {message.chat.id}")
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception as e:
        logging.error(f"Failed to delete message: {e}")
    bot.send_message(
        message.chat.id,
        "Привет! Я бот советов на каждый день 🌞\n\nВыбери, что хочешь получить:",
        reply_markup=get_keyboard()
    )
    logging.info(f"Sent response to /start for chat {message.chat.id}")

# -------------------------
# Хэндлер нажатий на inline-кнопки
# -------------------------
@bot.callback_query_handler(func=lambda c: True)
def callback_inline(c):
    if c.data == "daily":
        bot.answer_callback_query(c.id)
        phrase = get_daily_phrase(c.message.chat.id)
        text = f"📅 <b>Совет на сегодня:</b>\n\n{phrase}"
    elif c.data == "random":
        bot.answer_callback_query(c.id)
        phrase = get_random_phrase(c.message.chat.id)
        text = f"💡 <b>Совет:</b>\n\n{phrase}"
    else:
        return

    kb = get_keyboard()

    if c.message.text != text:
        try:
            bot.edit_message_text(
                chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                text=text,
                reply_markup=kb,
                disable_web_page_preview=True
            )
        except:
            bot.send_message(c.message.chat.id, text, reply_markup=kb)
    else:
        bot.answer_callback_query(c.id, "Совет дня уже выдан ✅")

    logging.info(f"User {c.message.chat.id} получил: {phrase}")

# -------------------------
# Запуск поллинга
# -------------------------
if __name__ == "__main__":
    logging.info("Starting polling")
    bot.infinity_polling()
