import os
import logging
from flask import Flask, request
import telebot

# -------------------------
# Логирование
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# -------------------------
# Конфигурация
# -------------------------
TOKEN = os.environ.get("TELEGRAM_TOKEN")
APP_URL = os.environ.get("WEBHOOK_URL")  # https://telegram-growth-bot-lrkb.onrender.com

if not TOKEN or not APP_URL:
    raise ValueError("❌ Укажи TELEGRAM_TOKEN и WEBHOOK_URL в переменных окружения!")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

# -------------------------
# Хэндлеры бота
# -------------------------
@bot.message_handler(commands=["start"])
def start_handler(message):
    bot.reply_to(message, "Привет 👋 Бот работает через Render!")

@bot.message_handler(commands=["help"])
def help_handler(message):
    bot.reply_to(message, "Я простой бот. Доступные команды: /start, /help")

@bot.message_handler(func=lambda m: True)
def echo_handler(message):
    bot.reply_to(message, f"Ты написал: {message.text}")

# -------------------------
# Flask эндпоинты
# -------------------------
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    json_str = request.get_data().decode("utf-8")
    logging.info(f"📩 Update received: {json_str}")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "ok", 200

@app.route("/", methods=["GET"])
def index():
    return "Бот работает!", 200

@app.route("/healthz", methods=["GET"])
def health():
    return "ok", 200

# -------------------------
# Запуск
# -------------------------
if __name__ == "__main__":
    # Устанавливаем вебхук (обновляем всегда)
    bot.remove_webhook()
    success = bot.set_webhook(url=f"{APP_URL}/{TOKEN}")
    if success:
        logging.info(f"✅ Webhook установлен: {APP_URL}/{TOKEN}")
    else:
        logging.error("❌ Ошибка установки вебхука")

    port = int(os.environ.get("PORT", 10000))
    logging.info(f"🚀 Flask сервер запущен на порту {port}")
    app.run(host="0.0.0.0", port=port)
