# bot.py — готовый для Render + Gunicorn
import os
import logging
import random
import threading
from time import sleep
from flask import Flask, request, jsonify
from telebot import TeleBot, types
from telebot.util import escape_markdown
import psycopg2
from psycopg2.pool import SimpleConnectionPool
import pendulum
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from threading import Lock
import atexit
import requests

# -----------------------
# Конфигурация окружения
# -----------------------
BOT_TOKEN = os.getenv('BOT_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
RENDER_EXTERNAL_HOSTNAME = os.getenv('RENDER_EXTERNAL_HOSTNAME')  # Например <service>.onrender.com
ADMIN_ID = int(os.getenv('TELEGRAM_ADMIN_ID', '0') or 0)
DEFAULT_TIMEZONE = os.getenv('BOT_TIMEZONE', 'UTC')
REMINDER_HOUR = os.getenv('REMINDER_HOUR', '09:00')
WEBHOOK_URL = os.getenv('WEBHOOK_URL') or (f'https://{RENDER_EXTERNAL_HOSTNAME}/webhook' if RENDER_EXTERNAL_HOSTNAME else None)
# Контроль запуска вебхука/шедулера при мульти-воркерах
WEBHOOK_WORKER = os.getenv('WEBHOOK_WORKER', '1')  # Установите '1' только для одного воркера, если используете несколько
SCHEDULER_LEADER = os.getenv('SCHEDULER_LEADER', '1')  # '1' — этот процесс запускает scheduler
WEBHOOK_CHECK_INTERVAL = int(os.getenv('WEBHOOK_CHECK_INTERVAL', '10'))  # минут

if not BOT_TOKEN or not DATABASE_URL:
    raise RuntimeError('Не заданы обязательные переменные окружения BOT_TOKEN или DATABASE_URL')

# -----------------------
# Логирование
# -----------------------
# Интеграция с gunicorn если есть
gunicorn_logger = logging.getLogger('gunicorn.error')
if gunicorn_logger.handlers:
    logging.root.handlers = gunicorn_logger.handlers
    logging.root.setLevel(gunicorn_logger.level or logging.INFO)
else:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

logger = logging.getLogger(__name__)
logger.info("Logger initialized")

# -----------------------
# Flask + TeleBot
# -----------------------
app = Flask(__name__)
bot = TeleBot(BOT_TOKEN)

# -----------------------
# DB connection pool
# -----------------------
db_pool = SimpleConnectionPool(1, 10, DATABASE_URL)

def get_conn():
    return db_pool.getconn()

def put_conn(conn):
    try:
        db_pool.putconn(conn)
    except Exception as e:
        logger.exception("put_conn error: %s", e)

@atexit.register
def close_pool():
    try:
        if db_pool:
            db_pool.closeall()
            logger.info("DB pool closed")
    except Exception:
        logger.exception("Error closing DB pool")

# -----------------------
# RateLimiter (простой)
# -----------------------
class RateLimiter:
    def __init__(self, max_calls=60, period=60):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
        self.lock = Lock()

    def __enter__(self):
        with self.lock:
            now = pendulum.now().timestamp()
            # очистка старых вызовов
            self.calls = [t for t in self.calls if now - t < self.period]
            if len(self.calls) >= self.max_calls:
                wait = self.period - (now - self.calls[0])
                if wait > 0:
                    logger.info("RateLimiter sleeping for %.2f seconds", wait)
                    sleep(wait)
            self.calls.append(now)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

rate_limiter = RateLimiter(max_calls=int(os.getenv('RATE_MAX_CALLS', '60')), period=int(os.getenv('RATE_PERIOD', '60')))

# -----------------------
# Уведомления и отправка
# -----------------------
def notify_admin_safe(text):
    if ADMIN_ID:
        try:
            bot.send_message(ADMIN_ID, text)
        except Exception:
            logger.exception("Failed to notify admin")

def send_message_with_rate_limit(chat_id, text, **kwargs):
    preview = text if len(text) < 200 else text[:200] + '...'
    logger.info("Attempting to send message to %s: %.200s", chat_id, preview)
    with rate_limiter:
        last_exc = None
        for attempt in range(1, 6):
            try:
                msg = bot.send_message(chat_id, text, **kwargs)
                logger.info("Message sent to %s message_id=%s", chat_id, getattr(msg, 'message_id', None))
                return msg
            except Exception as e:
                last_exc = e
                logger.warning("Send attempt %s/5 failed for %s: %s", attempt, chat_id, e)
                sleep(2 ** (attempt - 1))
        logger.error("Failed to send message to %s after retries: %s", chat_id, last_exc)
        if chat_id != ADMIN_ID:
            notify_admin_safe(f"⚠ Ошибка отправки сообщения для {chat_id}: {str(last_exc)[:400]}")
        return None

# -----------------------
# Инициализация БД + операции
# -----------------------
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
            logger.info("DB schema initialized")
    except Exception:
        logger.exception("init_db failed")
        notify_admin_safe("⚠ Ошибка инициализации БД. Проверьте логи.")
        raise
    finally:
        put_conn(conn)

def get_user(chat_id):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT chat_id, username, timezone, subscribed, last_menu_message_id FROM users WHERE chat_id = %s', (chat_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {
                'chat_id': row[0],
                'username': row[1],
                'timezone': row[2],
                'subscribed': row[3],
                'last_menu_message_id': row[4]
            }
    except Exception:
        logger.exception("get_user failed for %s", chat_id)
        return None
    finally:
        put_conn(conn)

def update_user(chat_id, **kwargs):
    # Простая реализация: пытаемся обновить, если не получилось - вставляем
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if kwargs:
                fields = ', '.join(f"{k} = %s" for k in kwargs.keys())
                values = list(kwargs.values()) + [chat_id]
                cur.execute(f"UPDATE users SET {fields} WHERE chat_id = %s", values)
                if cur.rowcount == 0:
                    # Вставка с полями, которые пришли (заполняем defaults)
                    cur.execute('''
                        INSERT INTO users (chat_id, username, timezone, subscribed, last_menu_message_id)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (chat_id) DO UPDATE SET
                          username = EXCLUDED.username,
                          timezone = EXCLUDED.timezone,
                          subscribed = EXCLUDED.subscribed,
                          last_menu_message_id = EXCLUDED.last_menu_message_id
                    ''', (
                        chat_id,
                        kwargs.get('username'),
                        kwargs.get('timezone', DEFAULT_TIMEZONE),
                        kwargs.get('subscribed', False),
                        kwargs.get('last_menu_message_id')
                    ))
            conn.commit()
            logger.info("User %s updated/created", chat_id)
    except Exception:
        logger.exception("update_user failed for %s", chat_id)
    finally:
        put_conn(conn)

def add_task(chat_id, task_date, completed=False):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO tasks (chat_id, task_date, completed) VALUES (%s, %s, %s)', (chat_id, task_date, completed))
            conn.commit()
            logger.info("Task added for %s %s", chat_id, task_date)
    except Exception:
        logger.exception("add_task failed for %s", chat_id)
    finally:
        put_conn(conn)

def get_tasks(chat_id, start_date, end_date):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT task_date, completed FROM tasks WHERE chat_id = %s AND task_date BETWEEN %s AND %s ORDER BY task_date', (chat_id, start_date, end_date))
            rows = cur.fetchall()
            return [{'task_date': r[0], 'completed': r[1]} for r in rows]
    except Exception:
        logger.exception("get_tasks failed for %s", chat_id)
        return []
    finally:
        put_conn(conn)

def cleanup_inactive_users():
    conn = get_conn()
    try:
        cutoff = pendulum.now('UTC').subtract(months=1)
        with conn.cursor() as cur:
            cur.execute('DELETE FROM users WHERE created_at < %s AND subscribed = FALSE', (cutoff,))
            deleted = cur.rowcount
            conn.commit()
            logger.info("cleanup_inactive_users deleted %s rows", deleted)
    except Exception:
        logger.exception("cleanup_inactive_users failed")
        notify_admin_safe("⚠ Ошибка очистки неактивных пользователей")
    finally:
        put_conn(conn)

# -----------------------
# Контент / клавиатуры
# -----------------------
MOTIVATIONAL_QUOTES = [
    "Каждый день — новый шанс стать лучше!",
    "Маленькие шаги приводят к большим целям!",
    "Ты сильнее, чем думаешь!",
]

def get_inline_keyboard(user):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton('✅ Сегодня', callback_data='today'),
        types.InlineKeyboardButton('📅 Следующий день', callback_data='next')
    )
    subscribe_text = '🔕 Отписаться' if user.get('subscribed', False) else '🔔 Подписаться'
    keyboard.add(types.InlineKeyboardButton(subscribe_text, callback_data='subscribe'))
    keyboard.add(types.InlineKeyboardButton('📊 Статистика', callback_data='stats'))
    keyboard.add(types.InlineKeyboardButton('🌍 Часовой пояс', callback_data='settimezone'))
    return keyboard

def send_menu(chat_id, user, text):
    logger.info("send_menu -> chat_id=%s text_preview=%.100s", chat_id, text)
    try:
        fresh_user = get_user(chat_id) or user or {'subscribed': False, 'timezone': DEFAULT_TIMEZONE}
        prev_id = fresh_user.get('last_menu_message_id')
        username = f"@{fresh_user.get('username')}" if fresh_user.get('username') else "друг"
        motivation = random.choice(MOTIVATIONAL_QUOTES)
        formatted_text = f"*{escape_markdown(text, version=2)}*\n\n_{escape_markdown(motivation, version=2)}_"
        if prev_id:
            try:
                bot.edit_message_text(
                    formatted_text,
                    chat_id,
                    prev_id,
                    parse_mode="MarkdownV2",
                    reply_markup=get_inline_keyboard(fresh_user)
                )
                logger.info("Menu updated for %s message_id=%s", chat_id, prev_id)
                return
            except Exception:
                logger.exception("edit_message_text failed, will send new message")
                update_user(chat_id, last_menu_message_id=None)
        msg = send_message_with_rate_limit(
            chat_id,
            formatted_text,
            parse_mode="MarkdownV2",
            reply_markup=get_inline_keyboard(fresh_user)
        )
        if msg:
            update_user(chat_id, last_menu_message_id=msg.message_id)
            logger.info("Menu sent for %s message_id=%s", chat_id, msg.message_id)
    except Exception:
        logger.exception("send_menu general error for %s", chat_id)
        try:
            send_message_with_rate_limit(chat_id, escape_markdown("⚠ Что-то пошло не так. Попробуй позже!", version=2), parse_mode="MarkdownV2")
        except Exception:
            notify_admin_safe(f"⚠ send_menu fatal for {chat_id}")

# -----------------------
# Команды / callbacks
# -----------------------
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    username = message.from_user.username or "друг"
    logger.info("/start from %s (@%s)", chat_id, username)
    update_user(chat_id, username=username)
    safe_username = escape_markdown(username, version=2)
    send_menu(chat_id, None, f"Привет, @{safe_username}! 👋 Я твой наставник по привычкам.")

@bot.message_handler(commands=['stats'])
def stats(message):
    chat_id = message.chat.id
    user = get_user(chat_id)
    if not user:
        send_message_with_rate_limit(chat_id, escape_markdown("⚠ Сначала начни с /start", version=2), parse_mode="MarkdownV2")
        return
    tz = user.get('timezone') or DEFAULT_TIMEZONE
    start_date = pendulum.now(tz).subtract(weeks=1).date()
    end_date = pendulum.now(tz).date()
    tasks = get_tasks(chat_id, start_date, end_date)
    completed = sum(1 for t in tasks if t['completed'])
    total = len(tasks)
    percentage = (completed / total * 100) if total > 0 else 0
    text = f"📊 Статистика за неделю:\n✅ Выполнено: {completed}/{total} ({percentage:.1f}%)"
    send_message_with_rate_limit(chat_id, escape_markdown(text, version=2), parse_mode="MarkdownV2")

@bot.message_handler(commands=['all_stats'])
def all_stats(message):
    chat_id = message.chat.id
    if chat_id != ADMIN_ID:
        logger.warning("Unauthorized /all_stats from %s", chat_id)
        return
    logger.info("Processing /all_stats by admin %s", chat_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM users WHERE subscribed = TRUE')
            subscribed = cur.fetchone()[0]
            cur.execute('SELECT COUNT(*) FROM tasks WHERE completed = TRUE')
            completed = cur.fetchone()[0]
            cur.execute('SELECT COUNT(*) FROM tasks')
            total = cur.fetchone()[0]
            percentage = (completed / total * 100) if total > 0 else 0
            text = f"📊 Общая статистика:\n👥 Подписчиков: {subscribed}\n✅ Выполнено задач: {completed}/{total} ({percentage:.1f}%)"
            send_message_with_rate_limit(chat_id, escape_markdown(text, version=2), parse_mode="MarkdownV2")
    except Exception:
        logger.exception("all_stats failed")
        notify_admin_safe("⚠ Ошибка в all_stats")
        send_message_with_rate_limit(chat_id, escape_markdown("⚠ Ошибка получения статистики", version=2), parse_mode="MarkdownV2")
    finally:
        put_conn(conn)

# -----------------------
# Scheduler
# -----------------------
scheduler = BackgroundScheduler()
try:
    hour, minute = map(int, REMINDER_HOUR.split(':'))
except Exception:
    hour, minute = 9, 0

# задача cleanup раз в сутки (запускается только если SCHEDULER_LEADER == '1')
if SCHEDULER_LEADER == '1':
    scheduler.add_job(cleanup_inactive_users, 'cron', hour=0, minute=0, timezone='UTC')

def send_menu_for_tz(timezone):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT chat_id FROM users WHERE subscribed = TRUE AND timezone = %s', (timezone,))
            rows = cur.fetchall()
            for (chat_id,) in rows:
                send_menu(chat_id, None, "🔔 Напоминание! Время работать над привычками!")
    except Exception:
        logger.exception("send_menu_for_tz failed for %s", timezone)
        notify_admin_safe(f"⚠ Ошибка напоминаний для {timezone}")
    finally:
        put_conn(conn)

# Добавляем напоминалки для популярных TZ (пример)
if SCHEDULER_LEADER == '1':
    for tz in ['Europe/Moscow', 'Europe/London', 'America/New_York', 'Asia/Tokyo', 'UTC']:
        scheduler.add_job(
            lambda tz=tz: send_menu_for_tz(tz),
            CronTrigger(hour=hour, minute=minute, timezone=tz)
        )
    scheduler.start()
    logger.info("Scheduler started (leader=%s)", SCHEDULER_LEADER)
else:
    logger.info("Scheduler not started in this process (SCHEDULER_LEADER!=1)")

# -----------------------
# Webhook helpers
# -----------------------
def ensure_webhook(max_retries=3, delay=3):
    if not WEBHOOK_URL:
        logger.warning("WEBHOOK_URL not configured; skipping ensure_webhook")
        return False
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo", timeout=10).json()
            if resp.get("ok"):
                info = resp["result"]
                url = info.get("url")
                pending = info.get("pending_update_count", 0)
                if not url:
                    logger.warning("Webhook not set (attempt %s/%s). Setting to %s", attempt, max_retries, WEBHOOK_URL)
                    res = bot.set_webhook(url=WEBHOOK_URL)
                    if res:
                        logger.info("Webhook set to %s", WEBHOOK_URL)
                    else:
                        logger.error("set_webhook returned False")
                    sleep(delay)
                else:
                    logger.info("Webhook active: %s (pending=%s)", url, pending)
                    if pending and ADMIN_ID:
                        notify_admin_safe(f"⚠ Внимание: в очереди Telegram осталось {pending} апдейтов.")
                    return True
            else:
                logger.error("getWebhookInfo returned not ok: %s", resp)
                notify_admin_safe(f"❌ Ошибка getWebhookInfo: {resp}")
        except Exception:
            logger.exception("ensure_webhook exception on attempt %s", attempt)
            sleep(delay)
    logger.error("Webhook did not become active after retries")
    notify_admin_safe("❌ Вебхук так и не установился после нескольких попыток.")
    return False

def setup_webhook_in_thread():
    def _setup():
        # небольшая задержка чтобы гарантировать, что процесс готов
        sleep(1)
        try:
            logger.info("Removing existing webhook (drop_pending_updates=True)")
            bot.remove_webhook(drop_pending_updates=True)
            sleep(1)
        except Exception:
            logger.exception("remove_webhook warning (non-fatal)")

        if WEBHOOK_URL:
            try:
                success = bot.set_webhook(url=WEBHOOK_URL)
                if success:
                    logger.info("Webhook successfully set to %s", WEBHOOK_URL)
                    ensure_webhook(max_retries=5, delay=2)
                    # Периодическая проверка webhook (в background)
                    def periodic_check():
                        while True:
                            ensure_webhook(max_retries=2, delay=1)
                            sleep(60 * WEBHOOK_CHECK_INTERVAL)
                    t = threading.Thread(target=periodic_check, daemon=True, name="webhook-check")
                    t.start()
                else:
                    logger.error("bot.set_webhook returned False")
                    notify_admin_safe("❌ Не удалось установить вебхук (set_webhook вернул False)")
            except Exception:
                logger.exception("Exception while setting webhook")
                notify_admin_safe("⚠ Ошибка установки webhook")
        else:
            logger.warning("WEBHOOK_URL not provided; webhook disabled (polling not used in this deployment)")
    thr = threading.Thread(target=_setup, daemon=True, name="webhook-setup-thread")
    thr.start()

# -----------------------
# Flask routes
# -----------------------
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        raw = request.get_data().decode('utf-8')
        if not raw:
            logger.warning("Empty webhook payload")
            return '', 400
        update = types.Update.de_json(raw)
        if update:
            bot.process_new_updates([update])
        return '', 200
    except Exception:
        logger.exception("Error processing webhook")
        return '', 500

@app.route('/')
def index():
    return jsonify({
        'status': 'ok',
        'pid': os.getpid(),
        'webhook_url': WEBHOOK_URL,
        'bot_token_set': bool(BOT_TOKEN)
    })

# -----------------------
# Инициализация при импорте (Gunicorn импортирует модуль)
# -----------------------
try:
    init_db()
except Exception:
    # Если DB упала — лучше аварийно завершить процесс (Gunicorn перезапустит если настроен)
    logger.exception("Fatal DB init error — aborting import")
    raise

# Запускаем webhook setup только в одном воркере (контролируем через WEBHOOK_WORKER)
if WEBHOOK_WORKER == '1':
    logger.info("WEBHOOK_WORKER=1 -> starting webhook setup thread")
    setup_webhook_in_thread()
else:
    logger.info("WEBHOOK_WORKER!=1 -> skipping webhook setup in this process")

# Рекомендуется также запускать scheduler только в одном процессе (SCHEDULER_LEADER)
if SCHEDULER_LEADER == '1':
    logger.info("SCHEDULER_LEADER=1 -> scheduler already started above if configured")
else:
    logger.info("SCHEDULER_LEADER!=1 -> scheduler not controlled by this process")

# Экспортируем app для Gunicorn (импортируемый модуль)
logger.info("Bot module imported, app ready (pid=%s)", os.getpid())
