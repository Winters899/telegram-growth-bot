import os
import random
import telebot
from flask import Flask, request
import logging
from logging.handlers import RotatingFileHandler
import json
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(),  # Вывод в консоль
        RotatingFileHandler('bot.log', maxBytes=1000000, backupCount=5)  # Сохранение в файл
    ]
)
logger = logging.getLogger(__name__)

# Проверка переменных окружения
TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = os.getenv("PORT", 5000)

logger.debug(f"Переменные окружения: BOT_TOKEN={'<скрыт>' if TOKEN else None}, "
             f"RENDER_EXTERNAL_URL={RENDER_EXTERNAL_URL}, PORT={PORT}")

if not TOKEN:
    logger.error("BOT_TOKEN не задан в переменных окружения")
    raise ValueError("BOT_TOKEN не задан")
if not RENDER_EXTERNAL_URL:
    logger.error("RENDER_EXTERNAL_URL не задан в переменных окружения")
    raise ValueError("RENDER_EXTERNAL_URL не задан")

# Инициализация бота и Flask
try:
    bot = telebot.TeleBot(TOKEN)
    logger.info("Бот успешно инициализирован")
except Exception as e:
    logger.error(f"Ошибка инициализации бота: {str(e)}")
    raise

app = Flask(__name__)
logger.info("Flask приложение инициализировано")

# Загрузка советов
advices = []
try:
    if os.path.exists("advices.txt"):
        with open("advices.txt", encoding="utf-8") as f:
            advices = [line.strip() for line in f if line.strip()]
            logger.info(f"Загружено {len(advices)} советов из advices.txt")
    else:
        advices = [
            "Пей больше воды",
            "Выходи гулять каждый день",
            "Высыпайся — сон лечит всё",
            "Веди дневник благодарности",
            "Учись чему-то новому каждый день",
            "Делай маленькие шаги к большой цели",
            "Меньше соцсетей — больше реальной жизни",
            "Занимайся спортом хотя бы 10 минут в день",
            "Медитируй и отдыхай от стресса",
            "Помогай другим — добро возвращается",
        ]
        logger.info(f"Файл advices.txt не найден, используются стандартные советы ({len(advices)})")
except Exception as e:
    logger.error(f"Ошибка при загрузке advices.txt: {str(e)}")
    raise

# Смайлы
emojis = ["🌟", "✨", "🔥", "💡", "🌈", "💖", "🌞", "🍀", "⚡", "🌊"]
logger.debug(f"Загружено {len(emojis)} эмодзи")

# Хэндлеры
@bot.message_handler(commands=["start"])
def start(msg):
    logger.info(f"Получена команда /start от {msg.from_user.id} (@{msg.from_user.username})")
    try:
        bot.reply_to(msg, "Привет! Я бот-советчик 🧙‍♂️\nНапиши /advice, и я дам совет!")
        logger.debug(f"Ответ на /start отправлен пользователю {msg.from_user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке ответа на /start: {str(e)}")

@bot.message_handler(commands=["advice"])
def advice(msg):
    logger.info(f"Получена команда /advice от {msg.from_user.id} (@{msg.from_user.username})")
    try:
        if random.randint(1, 5) == 1:  # шанс 1 из 5 — только смайл
            text = random.choice(emojis)
            logger.debug(f"Выбран только эмодзи: {text}")
        else:
            advice_text = random.choice(advices)
            emoji = random.choice(emojis)
            text = f"{advice_text} {emoji}"
            logger.debug(f"Выбран совет: {text}")
        bot.reply_to(msg, text)
        logger.debug(f"Ответ на /advice отправлен пользователю {msg.from_user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке ответа на /advice: {str(e)}")

# Обработчик для всех текстовых сообщений
@bot.message_handler(content_types=["text"])
def handle_text(msg):
    logger.info(f"Получено текстовое сообщение от {msg.from_user.id} (@{msg.from_user.username}): {msg.text}")
    try:
        bot.reply_to(msg, "Я понимаю только команды /start и /advice 😊")
        logger.debug(f"Ответ на текстовое сообщение отправлен пользователю {msg.from_user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке ответа на текстовое сообщение: {str(e)}")

# Flask endpoint для Telegram
@app.route("/webhook", methods=["POST"])
def webhook():
    logger.debug("Получен запрос на /webhook")
    try:
        json_str = request.get_data().decode("utf-8")
        logger.debug(f"Входящее обновление: {json_str}")
        update = telebot.types.Update.de_json(json_str)
        if update is None:
            logger.error("Не удалось декодировать обновление")
            return "ok", 200
        logger.info(f"Обновление успешно декодировано: update_id={update.update_id}")
        bot.process_new_updates([update])
        logger.debug("Обновление обработано")
        return "ok", 200
    except Exception as e:
        logger.error(f"Ошибка при обработке вебхука: {str(e)}")
        return "ok", 200

# Healthcheck
@app.route("/", methods=["GET"])
def index():
    logger.debug("Получен запрос на /")
    return "Бот работает!", 200

# Тестовый эндпоинт для отладки
@app.route("/test", methods=["POST"])
def test():
    logger.debug("Получен тестовый запрос на /test")
    try:
        data = request.get_data().decode("utf-8")
        logger.info(f"Тестовый запрос: {data}")
        return "Тест OK", 200
    except Exception as e:
        logger.error(f"Ошибка при обработке тестового запроса: {str(e)}")
        return "error", 500

if __name__ == "__main__":
    logger.info("Запуск приложения")
    # URL Render-а
    WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}/webhook"
    logger.debug(f"WEBHOOK_URL: {WEBHOOK_URL}")

    # Удаление старого вебхука
    try:
        bot.remove_webhook()
        logger.info("Старый вебхук успешно удален")
    except Exception as e:
        logger.error(f"Ошибка при удалении вебхука: {str(e)}")

    # Установка нового вебхука
    try:
        result = bot.set_webhook(url=WEBHOOK_URL)
        if result:
            logger.info(f"Вебхук успешно установлен: {WEBHOOK_URL}")
        else:
            logger.error("Не удалось установить вебхук")
    except Exception as e:
        logger.error(f"Ошибка при установке вебхука: {str(e)}")
        raise

    # Запуск Flask
    try:
        logger.info(f"Запуск Flask сервера на 0.0.0.0:{PORT}")
        app.run(host="0.0.0.0", port=int(PORT))
    except Exception as e:
        logger.error(f"Ошибка при запуске Flask сервера: {str(e)}")
        raise
