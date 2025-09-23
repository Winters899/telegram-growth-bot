import os
import telebot
from flask import Flask, request
from telebot import types
import random

# -------------------------
# Настройки
# -------------------------
TOKEN = os.environ["TELEGRAM_TOKEN"]
APP_URL = os.environ["WEBHOOK_URL"]

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

# -------------------------
# Загрузка фраз из файла
# -------------------------
try:
    with open("phrases.txt", "r", encoding="utf-8") as f:
        content = f.read()
    phrases = [p.strip() for p in content.split('---') if p.strip()]
except FileNotFoundError:
    phrases = ["Файл с фразами не найден! Добавь phrases.txt в проект."]

# -------------------------
# Хэндлер команды /start
# -------------------------
@bot.message_handler(commands=['start'])
def start_msg(message):
    keyboard = types.InlineKeyboardMarkup()
    start_button = types.InlineKeyboardButton(text="🚀 Получить мотивацию", callback_data="motivation")
    keyboard.add(start_button)
    
    bot.send_message(
        message.chat.id,
        "Привет! Я бот мотивации 😎\nНажми кнопку ниже, чтобы получить мотивацию:",
        reply_markup=keyboard
    )

# -------------------------
# Хэндлер нажатий на inline-кнопки
# -------------------------
@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    if call.data == "motivation":
        bot.answer_callback_query(call.id)
        phrase = random.choice(phrases)
        keyboard = types.InlineKeyboardMarkup()
        again_button = types.InlineKeyboardButton(text="🚀 Еще мотивация", callback_data="motivation")
        keyboard.add(again_button)
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=phrase,
                reply_markup=keyboard,
                disable_web_page_preview=True
            )
        except Exception:
            # если фраза та же или сообщение уже изменено
            bot.send_message(call.message.chat.id, phrase, reply_markup=keyboard)

# -------------------------
# Route для webhook
# -------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    json_str = request.get_data().decode("utf-8")
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
