import os
import telebot
from flask import Flask, request
from telebot import types

# -------------------------
# Настройки
# -------------------------
TOKEN = os.environ["TELEGRAM_TOKEN"]  # токен бота от BotFather
APP_URL = os.environ["WEBHOOK_URL"]   # https://<имя_приложения>.onrender.com

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# -------------------------
# Хэндлер команды /start
# -------------------------
@bot.message_handler(commands=['start'])
def start_msg(message):
    
keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
start_button = types.KeyboardButton(text="🚀 Начать")
keyboard.add(start_button)
bot.send_message(message.chat.id, "Привет! Я простой бот на Render 😎", reply_markup=keyboard)


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
