import os
import random
import telebot
from flask import Flask, request
import logging
from logging.handlers import RotatingFileHandler
from threading import Thread
from queue import Queue
import time
import signal
import sys

# --- Логирование ---
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler('bot.log', maxBytes=1000000, backupCount=5)
    ]
)
logger = logging.getLogger(__name__)

# --- Переменные окружения ---
TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", 10000))

if not TOKEN or not RENDER_EXTERNAL_URL:
    logger.error("BOT_TOKEN и RENDER_EXTERNAL_URL должны быть заданы!")
    raise ValueError("BOT_TOKEN и RENDER_EXTERNAL_URL должны быть заданы!")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# --- Загрузка советов ---
advices = []
if os.path.exists("advices.txt"):
    with open("advices.txt", encoding="utf-8") as f:
        advices = [line.strip() for line in f if line.strip()]
if not advices:
    advices = [
        "Пей больше воды",
        "Выходи гулять каждый день",
        "Высыпайся — сон лечит всё",
        "Веди дневник благодарности",
        "Учись чему-то новому каждый день"
    ]

emojis = ["🌟", "✨", "🔥", "💡", "🌈", "💖", "🌞", "🍀", "⚡", "🌊"]

# --- Очередь апдейтов ---
update_queue = Queue()

def worker():
    """Фоновый поток обработки апдейтов"""
    while True:
        update = update_queue.get()
        if update is None:  # сигнал завершения
            break
        try:
            bot.process_new_updates([update])
        except Exception as e:
            logger.error(f"Ошибка обработки апдейта: {e}")
        update_queue.task_done()

worker_thread = Thread(target=worker, daemon=True)
worker_thread.start()

# --- Хэндлеры бота ---
@bot.message_handler(commands=["start"])
def start(msg):
    bot.reply_to(msg, "Привет! Я бот-советчик 🧙‍♂️\nНапиши /advice, и я дам совет!")

@bot.message_handler(commands=["advice"])
def advice(msg):
    if random.randint(1, 5) == 1:
        text = random.choice(emojis)
    else:
        text = f"{random.choice(advices)} {random.choice(emojis)}"
    bot.reply_to(msg, text)

@bot.message_handler(content_types=["text"])
def handle_text(msg):
    bot.reply_to(msg, "Я понимаю только команды /start и /advice 😊")

# --- Webhook Flask endpoint ---
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        update_queue.put(update)
    except Exception as e:
        logger.error(f"Ошибка обработки вебхука: {e}")
    return "ok", 200

@app.route("/", methods=["GET"])
def index():
    return "Бот работает!", 200

# --- Обработчик сигналов завершения ---
def signal_handler(sig, frame):
    logger.info(f"Получен сигнал завершения: {sig}")
    update_queue.put(None)  # остановка воркера
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# --- Настройка и установка вебхука ---
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}/webhook"
try:
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    logger.info(f"Вебхук успешно установлен: {WEBHOOK_URL}")
except Exception as e:
    logger.error(f"Ошибка при установке вебхука: {e}")

# --- Запуск Flask ---
if __name__ == "__main__":
    logger.info(f"Запуск Flask сервера на 0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT)
