# -*- coding: utf-8 -*-
import os
import logging
import random
from time import sleep
from flask import Flask, request
from telebot import TeleBot, types
from telebot.util import escape_markdown
from psycopg2.pool import SimpleConnectionPool
import pendulum
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from threading import Lock
import atexit
import requests

# --- ЛОГИ ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# --- ОКРУЖЕНИЕ ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
RENDER_EXTERNAL_HOSTNAME = os.getenv('RENDER_EXTERNAL_HOSTNAME')
ADMIN_ID = int(os.getenv('TELEGRAM_ADMIN_ID', '0') or 0)
DEFAULT_TIMEZONE = os.getenv('BOT_TIMEZONE', 'UTC')
REMINDER_HOUR = os.getenv('REMINDER_HOUR', '09:00')
WEBHOOK_CHECK_INTERVAL = int(os.getenv('WEBHOOK_CHECK_INTERVAL', 10))

WEBHOOK_URL = f'https://{RENDER_EXTERNAL_HOSTNAME}/webhook' if RENDER_EXTERNAL_HOSTNAME else None

if not BOT_TOKEN or not DATABASE_URL:
    logging.error("❌ BOT_TOKEN и DATABASE_URL должны быть заданы")
    raise SystemExit(1)

# --- Flask и TeleBot ---
app = Flask(__name__)
bot = TeleBot(BOT_TOKEN)

# --- Пул соединений ---
db_pool = SimpleConnectionPool(1, 10, DATABASE_URL)

def get_conn():
    return db_pool.getconn()

def put_conn(conn):
    db_pool.putconn(conn)

@atexit.register
def close_pool():
    if db_pool:
        db_pool.closeall()
        logging.info("Все соединения с базой закрыты.")

# --- RateLimiter ---
class RateLimiter:
    def __init__(self, max_calls=60, period=60):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
        self.lock = Lock()

    def __enter__(self):
        with self.lock:
            now = pendulum.now().timestamp()
            self.calls = [t for t in self.calls if now - t < self.period]
            if len(self.calls) >= self.max_calls:
                wait = self.period - (now - self.calls[0])
                if wait > 0:
                    sleep(wait)
            self.calls.append(now)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

rate_limiter = RateLimiter()

# --- Уведомления ---
def notify_admin_safe(text):
    if ADMIN_ID:
        try:
            bot.send_message(ADMIN_ID, text)
        except Exception as e:
            logging.error(f"Не удалось уведомить админа: {e}")

def send_message_with_rate_limit(chat_id, text, **kwargs):
    with rate_limiter:
        last_exc = None
        for attempt in range(5):
            try:
                return bot.send_message(chat_id, text, **kwargs)
            except Exception as e:
                last_exc = e
                logging.warning(f"Попытка {attempt+1}/5 не удалась: {e}")
                sleep(2 ** attempt)
        logging.error(f"Ошибка отправки сообщения {chat_id}: {last_exc}")
        if chat_id != ADMIN_ID:
            notify_admin_safe(f"⚠ Ошибка отправки сообщения для {chat_id}: {str(last_exc)[:200]}")
        return None

# --- Работа с БД ---
def init_db():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    chat_id BIGINT PRIMARY KEY,
                    username TEXT,
                    timezone TEXT DEFAULT %s,
                    subscribed BOOLEAN DEFAULT FALSE,
                    last_menu_message_id BIGINT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''', (DEFAULT_TIMEZONE,))
            cur.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT,
                    task_date DATE,
                    completed BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (chat_id) REFERENCES users (chat_id) ON DELETE CASCADE
                )
            ''')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_tasks_chat_id_date ON tasks (chat_id, task_date)')
        conn.commit()
    except Exception as e:
        logging.error(f'Ошибка init_db: {e}')
        notify_admin_safe(f'⚠ Ошибка init_db: {e}')
        raise
    finally:
        put_conn(conn)

def get_user(chat_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT chat_id, username, timezone, subscribed, last_menu_message_id FROM users WHERE chat_id=%s', (chat_id,))
            user = cur.fetchone()
        return {
            'chat_id': user[0],
            'username': user[1],
            'timezone': user[2],
            'subscribed': user[3],
            'last_menu_message_id': user[4]
        } if user else None
    finally:
        put_conn(conn)

def update_user(chat_id, **kwargs):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''
                INSERT INTO users (chat_id, username, timezone, subscribed, last_menu_message_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (chat_id) DO UPDATE SET
                    username=EXCLUDED.username,
                    timezone=EXCLUDED.timezone,
                    subscribed=EXCLUDED.subscribed,
                    last_menu_message_id=EXCLUDED.last_menu_message_id
            ''', (
                chat_id,
                kwargs.get('username'),
                kwargs.get('timezone', DEFAULT_TIMEZONE),
                kwargs.get('subscribed', False),
                kwargs.get('last_menu_message_id')
            ))
        conn.commit()
    except Exception as e:
        logging.error(f"Ошибка update_user: {e}")
    finally:
        put_conn(conn)

# --- Мотивация ---
MOTIVATIONAL_QUOTES = [
    "Каждый день — новый шанс стать лучше!",
    "Маленькие шаги приводят к большим целям!",
    "Ты сильнее, чем думаешь!",
]

# --- Меню ---
def get_inline_keyboard(user):
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Сегодня", callback_data="today"),
        types.InlineKeyboardButton("📅 Следующий день", callback_data="next")
    )
    sub_text = "🔕 Отписаться" if user.get("subscribed") else "🔔 Подписаться"
    kb.add(types.InlineKeyboardButton(sub_text, callback_data="subscribe"))
    kb.add(types.InlineKeyboardButton("📊 Статистика", callback_data="stats"))
    kb.add(types.InlineKeyboardButton("🌍 Часовой пояс", callback_data="settimezone"))
    return kb

def send_menu(chat_id, user, text):
    fresh_user = get_user(chat_id) or user or {'subscribed': False, 'timezone': DEFAULT_TIMEZONE}
    prev_id = fresh_user.get("last_menu_message_id")
    motivation = random.choice(MOTIVATIONAL_QUOTES)
    formatted_text = f"*{escape_markdown(text, version=2)}*\n\n_{escape_markdown(motivation, version=2)}_"

    try:
        if prev_id:
            try:
                bot.edit_message_text(
                    formatted_text,
                    chat_id,
                    prev_id,
                    parse_mode="MarkdownV2",
                    reply_markup=get_inline_keyboard(fresh_user)
                )
                return
            except:
                update_user(chat_id, last_menu_message_id=None)

        msg = send_message_with_rate_limit(
            chat_id,
            formatted_text,
            parse_mode="MarkdownV2",
            reply_markup=get_inline_keyboard(fresh_user)
        )
        if msg:
            update_user(chat_id, last_menu_message_id=msg.message_id)
    except Exception as e:
        logging.error(f"Ошибка send_menu: {e}")
        send_message_with_rate_limit(chat_id, "⚠ Что-то пошло не так.", parse_mode="MarkdownV2")

# --- Команды ---
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    username = message.from_user.username or "друг"
    update_user(chat_id, username=username)
    send_menu(chat_id, None, f"Привет, @{username}! 👋 Я твой наставник по привычкам.")

# --- Планировщик ---
scheduler = BackgroundScheduler()
try:
    hour, minute = map(int, REMINDER_HOUR.split(":"))
except:
    hour, minute = 9, 0
scheduler.add_job(lambda: cleanup_inactive_users(), 'cron', hour=0, minute=0, timezone="UTC")

# --- Webhook ---
@app.route('/webhook', methods=['POST'])
def webhook():
    update = types.Update.de_json(request.get_json())
    if update:
        bot.process_new_updates([update])
    return "", 200

@app.route('/')
def index():
    return "Бот работает!"

# --- Ensure Webhook ---
def ensure_webhook(max_retries=3, delay=3):
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo").json()
            if resp.get("ok"):
                info = resp["result"]
                if not info.get("url"):
                    bot.set_webhook(url=WEBHOOK_URL)
                    sleep(delay)
                return True
        except Exception as e:
            logging.error(f"ensure_webhook ошибка: {e}")
            notify_admin_safe(f"⚠ ensure_webhook ошибка: {e}")
        sleep(delay)
    return False

# --- Запуск ---
if __name__ == "__main__":
    init_db()
    if WEBHOOK_URL:
        try:
            bot.remove_webhook(drop_pending_updates=True)
            sleep(1)
            if bot.set_webhook(url=WEBHOOK_URL):
                ensure_webhook()
                scheduler.add_job(ensure_webhook, "interval", minutes=WEBHOOK_CHECK_INTERVAL)
                scheduler.start()
        except Exception as e:
            logging.error(f"Ошибка установки webhook: {e}")
            notify_admin_safe(f"⚠ Ошибка webhook: {e}")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
