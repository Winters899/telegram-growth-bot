import os
import re
import json
import telebot
import schedule
import time
import threading
import logging
from flask import Flask, request
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor
from telebot import types
from telebot.formatting import escape_markdown
from telebot.types import Update
from datetime import datetime, timedelta, timezone
import pendulum
import random
from collections import deque
from time import monotonic

# ---------------------- Логирование ----------------------
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# ---------------------- Flask-приложение ----------------------
app = Flask(__name__)

# ---------------------- Настройка бота ----------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения!")

bot = telebot.TeleBot(BOT_TOKEN)

# ---------------------- Настройка базы данных ----------------------
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")

db_pool = None
if DB_NAME and DB_USER and DB_PASSWORD:
    try:
        db_pool = SimpleConnectionPool(
            minconn=1,
            maxconn=5,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            cursor_factory=RealDictCursor
        )
        logger.info("Подключение к БД установлено")
    except Exception as e:
        logger.error(f"Ошибка подключения к БД: {e}")
else:
    logger.warning("Параметры подключения к БД не заданы — БД не используется")

# ---------------------- Вебхук ----------------------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    raw = request.data.decode("utf-8")
    try:
        update = Update.de_json(json.loads(raw))

        chat_id = None
        if update.message:
            chat_id = update.message.chat.id
        elif update.callback_query:
            chat_id = update.callback_query.message.chat.id

        logger.info(
            f"Обработка обновления {update.update_id}, chat_id={chat_id}"
        )

        bot.process_new_updates([update])
    except Exception as e:
        logger.exception("Ошибка обработки обновления: %s", e)
    return "ok", 200

# ---------------------- Команды ----------------------
@bot.message_handler(commands=['start'])
def start_handler(message):
    bot.reply_to(message, "Привет 👋 Я бот, и я работаю!")

@bot.message_handler(commands=['help'])
def help_handler(message):
    text = (
        "Доступные команды:\n"
        "/start — запустить бота\n"
        "/help — помощь\n"
        "/echo <текст> — повторю твой текст\n"
    )
    bot.reply_to(message, text)

@bot.message_handler(commands=['echo'])
def echo_handler(message):
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        bot.reply_to(message, f"Ты сказал: {escape_markdown(args[1])}", parse_mode="MarkdownV2")
    else:
        bot.reply_to(message, "Ты ничего не написал 🤷")

# ---------------------- Фоновый планировщик ----------------------
def scheduled_task():
    logger.info("Запущена фоновая задача (пример)")

schedule.every(1).hours.do(scheduled_task)

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_scheduler, daemon=True).start()

# ---------------------- Установка вебхука ----------------------
def setup_webhook():
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if render_url:
        full_webhook_url = f"{render_url}/{BOT_TOKEN}"
        bot.remove_webhook()
        time.sleep(1)
        success = bot.set_webhook(url=full_webhook_url, timeout=60)
        if success:
            logger.info(f"Вебхук установлен: {full_webhook_url}")
        else:
            logger.error("Не удалось установить вебхук")
    else:
        logger.warning("RENDER_EXTERNAL_URL не задан — запуск в режиме polling")
        bot.remove_webhook()
        bot.infinity_polling(timeout=60, long_polling_timeout=30)

# ---------------------- Основной запуск ----------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    setup_webhook()
    logger.info(f"Запуск Flask на порту {port}")
    app.run(host="0.0.0.0", port=port)
