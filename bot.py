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

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# -------------------------
# Загрузка фраз из файла
# -------------------------
with open("phrases.txt", "r", encoding="utf-8") as f:
    content = f.read()
phrases = [p.strip() for p in content.split('---') if p.strip()]

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
        # Меняем текст существующего сообщения
        keyboard = types.InlineKeyboardMarkup()
        start_button = types.InlineKeyboardButton(text="🚀 Еще мотивация", callback_data="motivation")
        keyboard.add(start_button)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=phrase,
            reply_markup=keyboard
        )

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
# Установка webhook при старте
# -------------------------
bot.remove_webhook()
bot.set_webhook(url=f"{APP_URL}/webhook")

# -------------------------
# Запуск Flask на Render
# -------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
