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
    handlers=[logging.StreamHandler(), RotatingFileHandler('bot.log', maxBytes=1_000_000, backupCount=3)]
)
logger = logging.getLogger(__name__)

# Конфигурация
app = Flask(__name__)
bot = telebot.TeleBot(os.getenv("BOT_TOKEN") or (logger.error("BOT_TOKEN отсутствует!") or exit(1)))
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL") or (logger.error("RENDER_EXTERNAL_URL отсутствует!") or exit(1))
PORT = int(os.getenv("PORT", 10000))

# Загрузка советов
def load_advices(file_path="advices.txt"):
    try:
        with open(file_path, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()] or _default_advices()
    except FileNotFoundError:
        return _default_advices()

def _default_advices():
    return ["Пей больше воды", "Выходи гулять", "Высыпайся", "Веди дневник благодарности", "Учись новому"]

advices = load_advices()
emojis = ["🌟", "✨", "🔥", "💡", "🌈"]

# Очередь апдейтов
update_queue = Queue(maxsize=100)  # Ограничение размера очереди

def process_updates():
    while True:
        try:
            update = update_queue.get(timeout=10)  # Таймаут для избежания зависания
            if update is None: break
            bot.process_new_updates([update])
            update_queue.task_done()
        except Exception as e:
            logger.error(f"Ошибка обработки апдейта: {e}")

Thread(target=process_updates, daemon=True).start()

# Хэндлеры бота
@bot.message_handler(commands=["start"])
def start(msg):
    logger.info(f"Получена команда /start от {msg.from_user.id}")
    bot.reply_to(msg, "Привет! Я бот-советчик 🧙‍♂️\nНапиши /advice для совета!")

@bot.message_handler(commands=["advice"])
def advice(msg):
    logger.info(f"Получена команда /advice от {msg.from_user.id}")
    text = random.choice(emojis) if random.random() < 0.2 else f"{random.choice(advices)} {random.choice(emojis)}"
    bot.reply_to(msg, text)

@bot.message_handler(content_types=["text"])
def handle_text(msg):
    logger.info(f"Получен текст от {msg.from_user.id}: {msg.text}")
    bot.reply_to(msg, "Понимаю только /start и /advice 😊")

# Webhook
@app.post("/webhook")
def webhook():
    try:
        data = request.get_data().decode("utf-8")
        logger.debug(f"Получен апдейт: {data}")
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

# Установка вебхука с повторными попытками
def set_webhook_with_retry(attempts=3, delay=5):
    for attempt in range(attempts):
        try:
            bot.set_webhook(url=f"{WEBHOOK_URL}/webhook", drop_pending_updates=True)
            logger.info(f"Вебхук установлен: {WEBHOOK_URL}/webhook")
            return
        except Exception as e:
            logger.error(f"Попытка {attempt + 1}/{attempts} установки вебхука не удалась: {e}")
            if attempt < attempts - 1:
                import time
                time.sleep(delay)
    logger.error("Не удалось установить вебхук")
    exit(1)

# Запуск
if __name__ == "__main__":
    set_webhook_with_retry()
    logger.info(f"Сервер запущен на 0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
