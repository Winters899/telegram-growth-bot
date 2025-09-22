import os
import random
import logging
from logging.handlers import RotatingFileHandler
from queue import Queue
from threading import Thread
from flask import Flask, request
import telebot

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(), RotatingFileHandler('bot.log', maxBytes=1_000_000, backupCount=2)]
)
logger = logging.getLogger(__name__)

# Конфигурация
app = Flask(__name__)
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", 10000))

if not (TOKEN and WEBHOOK_URL):
    logger.error("BOT_TOKEN или RENDER_EXTERNAL_URL отсутствует!")
    exit(1)

bot = telebot.TeleBot(TOKEN)

# Загрузка советов
def load_advices(file_path="advices.txt"):
    try:
        with open(file_path, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()] or _default_advices()
    except FileNotFoundError:
        return _default_advices()

def _default_advices():
    return ["Пей больше воды", "Выходи гулять", "Высыпайся"]

advices = load_advices()
emojis = ["🌟", "✨", "🔥"]

# Очередь апдейтов
update_queue = Queue(maxsize=50)

def process_updates():
    while True:
        try:
            update = update_queue.get(timeout=5)
            if update is None: break
            bot.process_new_updates([update])
            update_queue.task_done()
        except Exception as e:
            logger.error(f"Ошибка обработки апдейта: {e}")

Thread(target=process_updates, daemon=True).start()

# Хэндлеры бота
@bot.message_handler(commands=["start"])
def start(msg):
    logger.info(f"Команда /start от {msg.from_user.id}")
    bot.reply_to(msg, "Привет! Я бот-советчик 🧙‍♂️\nНапиши /advice для совета!")

@bot.message_handler(commands=["advice"])
def advice(msg):
    logger.info(f"Команда /advice от {msg.from_user.id}")
    text = random.choice(emojis) if random.random() < 0.2 else f"{random.choice(advices)} {random.choice(emojis)}"
    bot.reply_to(msg, text)

@bot.message_handler(content_types=["text"])
def handle_text(msg):
    logger.info(f"Текст от {msg.from_user.id}: {msg.text}")
    bot.reply_to(msg, "Понимаю только /start и /advice 😊")

# Webhook
@app.post("/webhook")
def webhook():
    try:
        data = request.get_data().decode("utf-8")
        logger.debug(f"Получен апдейт: {data[:50]}...")
        update = telebot.types.Update.de_json(data)
        if update:
            update_queue.put(update)
            return "ok", 200
        logger.warning("Пустой апдейт")
        return "ok", 200
    except Exception as e:
        logger.error(f"Ошибка вебхука: {e}")
        return "error", 500

@app.get("/")
def index():
    return "Бот работает!", 200

# Установка вебхука
def set_webhook():
    try:
        bot.set_webhook(url=f"{WEBHOOK_URL}/webhook", drop_pending_updates=True, timeout=10)
        webhook_info = bot.get_webhook_info()
        logger.info(f"Webhook info: {webhook_info.url}, pending updates: {webhook_info.pending_update_count}")
    except Exception as e:
        logger.error(f"Ошибка установки вебхука: {e}")
        exit(1)

if __name__ == "__main__":
    set_webhook()
    logger.info(f"Сервер запущен на 0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
