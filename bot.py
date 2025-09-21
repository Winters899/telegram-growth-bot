import os
import logging
import json
import random
import threading
from time import sleep
from flask import Flask, request, jsonify
from telebot import TeleBot, types
from telebot.util import escape_markdown
from telebot.apihelper import ApiTelegramException
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
RENDER_EXTERNAL_HOSTNAME = os.getenv('RENDER_EXTERNAL_HOSTNAME')  # Например, <service>.onrender.com
ADMIN_ID = int(os.getenv('TELEGRAM_ADMIN_ID', '0') or 0)
DEFAULT_TIMEZONE = os.getenv('BOT_TIMEZONE', 'UTC')
REMINDER_HOUR = os.getenv('REMINDER_HOUR', '09:00')
WEBHOOK_URL = os.getenv('WEBHOOK_URL') or (f'https://{RENDER_EXTERNAL_HOSTNAME}/webhook' if RENDER_EXTERNAL_HOSTNAME else None)
WEBHOOK_WORKER = os.getenv('WEBHOOK_WORKER', '1')  # Установите '1' только для одного воркера
SCHEDULER_LEADER = os.getenv('SCHEDULER_LEADER', '1')  # '1' — этот процесс запускает scheduler
WEBHOOK_CHECK_INTERVAL = int(os.getenv('WEBHOOK_CHECK_INTERVAL', '10'))  # минут

if not BOT_TOKEN or not DATABASE_URL:
    raise RuntimeError('Не заданы обязательные переменные окружения BOT_TOKEN или DATABASE_URL')

# -----------------------
# Логирование
# -----------------------
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            'timestamp': pendulum.now('UTC').isoformat(),
            'level': record.levelname,
            'message': record.getMessage(),
            'module': record.module,
            'funcName': record.funcName,
            'line': record.lineno,
            'pid': os.getpid(),
        }
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        if hasattr(record, 'extra'):
            log_data.update(getattr(record, 'extra', {}))
        return json.dumps(log_data, ensure_ascii=False)

gunicorn_logger = logging.getLogger('gunicorn.error')
if gunicorn_logger.handlers:
    logging.root.handlers = gunicorn_logger.handlers
    logging.root.setLevel(gunicorn_logger.level or logging.INFO)
else:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.root.handlers = [handler]
    logging.root.setLevel(logging.INFO)

logger = logging.getLogger(__name__)
logger.info("Logger initialized with JSON format")

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
    start_time = pendulum.now('UTC')
    conn = db_pool.getconn()
    duration = (pendulum.now('UTC') - start_time).total_seconds()
    logger.debug("Acquired DB connection", extra={
        'duration_s': duration,
        'pool_stats': {
            'min': db_pool._minconn,
            'max': db_pool._maxconn,
            'used': db_pool._used,
            'free': db_pool._pool.qsize()
        }
    })
    return conn

def put_conn(conn):
    try:
        db_pool.putconn(conn)
        logger.debug("Returned DB connection", extra={
            'pool_stats': {
                'min': db_pool._minconn,
                'max': db_pool._maxconn,
                'used': db_pool._used,
                'free': db_pool._pool.qsize()
            }
        })
    except Exception as e:
        logger.exception("put_conn error", extra={'error': str(e)})

@atexit.register
def close_pool():
    try:
        if db_pool:
            db_pool.closeall()
            logger.info("DB pool closed")
    except Exception:
        logger.exception("Error closing DB pool")

# -----------------------
# RateLimiter
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
            self.calls = [t for t in self.calls if now - t < self.period]
            if len(self.calls) >= self.max_calls:
                wait = self.period - (now - self.calls[0])
                if wait > 0:
                    logger.info("RateLimiter sleeping", extra={'wait_s': wait, 'calls': len(self.calls)})
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
            logger.info("Admin notified", extra={'admin_id': ADMIN_ID, 'text_preview': text[:200]})
        except Exception as e:
            logger.exception("Failed to notify admin", extra={'admin_id': ADMIN_ID, 'error': str(e)})

def send_message_with_rate_limit(chat_id, text, **kwargs):
    start_time = pendulum.now('UTC')
    preview = text if len(text) < 200 else text[:200] + '...'
    logger.info("Attempting to send message", extra={'chat_id': chat_id, 'text_preview': preview, 'kwargs': kwargs})
    with rate_limiter:
        last_exc = None
        for attempt in range(1, 6):
            try:
                msg = bot.send_message(chat_id, text, **kwargs)
                duration = (pendulum.now('UTC') - start_time).total_seconds()
                logger.info("Message sent successfully", extra={
                    'chat_id': chat_id,
                    'message_id': getattr(msg, 'message_id', None),
                    'attempt': attempt,
                    'duration_s': duration
                })
                return msg
            except ApiTelegramException as e:
                last_exc = e
                logger.warning("Send attempt failed (Telegram API)", extra={
                    'chat_id': chat_id,
                    'attempt': attempt,
                    'error_code': e.error_code,
                    'error_description': e.description,
                    'duration_s': (pendulum.now('UTC') - start_time).total_seconds()
                })
                if e.error_code == 429:
                    sleep_time = e.result_json.get('parameters', {}).get('retry_after', 1)
                    logger.warning("Rate limit hit, sleeping", extra={'chat_id': chat_id, 'sleep_s': sleep_time})
                    sleep(sleep_time)
                elif e.error_code == 403:
                    logger.warning("Bot blocked by user", extra={'chat_id': chat_id})
                    update_user(chat_id, subscribed=False)
                    return None
                sleep(2 ** (attempt - 1))
            except Exception as e:
                last_exc = e
                logger.warning("Send attempt failed", extra={
                    'chat_id': chat_id,
                    'attempt': attempt,
                    'error': str(e),
                    'duration_s': (pendulum.now('UTC') - start_time).total_seconds()
                })
                sleep(2 ** (attempt - 1))
        duration = (pendulum.now('UTC') - start_time).total_seconds()
        logger.error("Failed to send message after retries", extra={
            'chat_id': chat_id,
            'error': str(last_exc),
            'duration_s': duration
        })
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
            start_time = pendulum.now('UTC')
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
            duration = (pendulum.now('UTC') - start_time).total_seconds()
            logger.info("DB schema initialized", extra={'duration_s': duration})
    except Exception as e:
        logger.exception("init_db failed", extra={'error': str(e)})
        notify_admin_safe("⚠ Ошибка инициализации БД. Проверьте логи.")
        raise
    finally:
        put_conn(conn)

def get_user(chat_id):
    conn = get_conn()
    try:
        start_time = pendulum.now('UTC')
        with conn.cursor() as cur:
            cur.execute('SELECT chat_id, username, timezone, subscribed, last_menu_message_id FROM users WHERE chat_id = %s', (chat_id,))
            row = cur.fetchone()
            duration = (pendulum.now('UTC') - start_time).total_seconds()
            if not row:
                logger.info("User not found", extra={'chat_id': chat_id, 'duration_s': duration})
                return None
            logger.debug("User retrieved", extra={'chat_id': chat_id, 'duration_s': duration})
            return {
                'chat_id': row[0],
                'username': row[1],
                'timezone': row[2],
                'subscribed': row[3],
                'last_menu_message_id': row[4]
            }
    except Exception as e:
        logger.exception("get_user failed", extra={'chat_id': chat_id, 'error': str(e)})
        return None
    finally:
        put_conn(conn)

def update_user(chat_id, **kwargs):
    conn = get_conn()
    try:
        start_time = pendulum.now('UTC')
        with conn.cursor() as cur:
            if kwargs:
                fields = ', '.join(f"{k} = %s" for k in kwargs.keys())
                values = list(kwargs.values()) + [chat_id]
                cur.execute(f"UPDATE users SET {fields} WHERE chat_id = %s", values)
                if cur.rowcount == 0:
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
            duration = (pendulum.now('UTC') - start_time).total_seconds()
            logger.info("User updated/created", extra={'chat_id': chat_id, 'fields': list(kwargs.keys()), 'duration_s': duration})
    except Exception as e:
        logger.exception("update_user failed", extra={'chat_id': chat_id, 'error': str(e)})
    finally:
        put_conn(conn)

def add_task(chat_id, task_date, completed=False):
    conn = get_conn()
    try:
        start_time = pendulum.now('UTC')
        with conn.cursor() as cur:
            cur.execute('INSERT INTO tasks (chat_id, task_date, completed) VALUES (%s, %s, %s)', (chat_id, task_date, completed))
            conn.commit()
            duration = (pendulum.now('UTC') - start_time).total_seconds()
            logger.info("Task added", extra={'chat_id': chat_id, 'task_date': str(task_date), 'duration_s': duration})
    except Exception as e:
        logger.exception("add_task failed", extra={'chat_id': chat_id, 'task_date': str(task_date), 'error': str(e)})
    finally:
        put_conn(conn)

def get_tasks(chat_id, start_date, end_date):
    conn = get_conn()
    try:
        start_time = pendulum.now('UTC')
        with conn.cursor() as cur:
            cur.execute('SELECT task_date, completed FROM tasks WHERE chat_id = %s AND task_date BETWEEN %s AND %s ORDER BY task_date', (chat_id, start_date, end_date))
            rows = cur.fetchall()
            duration = (pendulum.now('UTC') - start_time).total_seconds()
            logger.debug("Tasks retrieved", extra={'chat_id': chat_id, 'task_count': len(rows), 'duration_s': duration})
            return [{'task_date': r[0], 'completed': r[1]} for r in rows]
    except Exception as e:
        logger.exception("get_tasks failed", extra={'chat_id': chat_id, 'start_date': str(start_date), 'end_date': str(end_date), 'error': str(e)})
        return []
    finally:
        put_conn(conn)

def cleanup_inactive_users():
    conn = get_conn()
    try:
        start_time = pendulum.now('UTC')
        cutoff = pendulum.now('UTC').subtract(months=1)
        with conn.cursor() as cur:
            cur.execute('DELETE FROM users WHERE created_at < %s AND subscribed = FALSE', (cutoff,))
            deleted = cur.rowcount
            conn.commit()
            duration = (pendulum.now('UTC') - start_time).total_seconds()
            logger.info("cleanup_inactive_users completed", extra={'deleted_rows': deleted, 'duration_s': duration})
    except Exception as e:
        logger.exception("cleanup_inactive_users failed", extra={'error': str(e)})
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
    logger.info("send_menu started", extra={'chat_id': chat_id, 'text_preview': text[:100]})
    try:
        start_time = pendulum.now('UTC')
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
                duration = (pendulum.now('UTC') - start_time).total_seconds()
                logger.info("Menu updated", extra={'chat_id': chat_id, 'message_id': prev_id, 'duration_s': duration})
                return
            except Exception as e:
                logger.exception("edit_message_text failed, sending new message", extra={'chat_id': chat_id, 'message_id': prev_id, 'error': str(e)})
                update_user(chat_id, last_menu_message_id=None)
        msg = send_message_with_rate_limit(
            chat_id,
            formatted_text,
            parse_mode="MarkdownV2",
            reply_markup=get_inline_keyboard(fresh_user)
        )
        if msg:
            update_user(chat_id, last_menu_message_id=msg.message_id)
            duration = (pendulum.now('UTC') - start_time).total_seconds()
            logger.info("Menu sent", extra={'chat_id': chat_id, 'message_id': msg.message_id, 'duration_s': duration})
    except Exception as e:
        logger.exception("send_menu general error", extra={'chat_id': chat_id, 'error': str(e)})
        try:
            send_message_with_rate_limit(chat_id, escape_markdown("⚠ Что-то пошло не так. Попробуй позже!", version=2), parse_mode="MarkdownV2")
        except Exception as e:
            logger.exception("send_menu fatal error", extra={'chat_id': chat_id, 'error': str(e)})
            notify_admin_safe(f"⚠ send_menu fatal for {chat_id}: {str(e)[:400]}")

# -----------------------
# Команды / callbacks
# -----------------------
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    username = message.from_user.username or "друг"
    logger.info("/start command", extra={'chat_id': chat_id, 'username': username})
    update_user(chat_id, username=username)
    safe_username = escape_markdown(username, version=2)
    send_menu(chat_id, None, f"Привет, @{safe_username}! 👋 Я твой наставник по привычкам.")

@bot.message_handler(commands=['stats'])
def stats(message):
    chat_id = message.chat.id
    logger.info("/stats command", extra={'chat_id': chat_id})
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
        logger.warning("Unauthorized /all_stats attempt", extra={'chat_id': chat_id})
        return
    logger.info("Processing /all_stats", extra={'chat_id': chat_id})
    conn = get_conn()
    try:
        start_time = pendulum.now('UTC')
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
            duration = (pendulum.now('UTC') - start_time).total_seconds()
            logger.info("all_stats completed", extra={'chat_id': chat_id, 'duration_s': duration})
    except Exception as e:
        logger.exception("all_stats failed", extra={'chat_id': chat_id, 'error': str(e)})
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
    logger.warning("Invalid REMINDER_HOUR format, using default 09:00", extra={'REMINDER_HOUR': REMINDER_HOUR})

if SCHEDULER_LEADER == '1':
    scheduler.add_job(cleanup_inactive_users, 'cron', hour=0, minute=0, timezone='UTC')
    logger.info("Scheduled cleanup_inactive_users job")

def send_menu_for_tz(timezone):
    conn = get_conn()
    try:
        start_time = pendulum.now('UTC')
        with conn.cursor() as cur:
            cur.execute('SELECT chat_id FROM users WHERE subscribed = TRUE AND timezone = %s', (timezone,))
            rows = cur.fetchall()
            duration = (pendulum.now('UTC') - start_time).total_seconds()
            logger.info("send_menu_for_tz started", extra={'timezone': timezone, 'user_count': len(rows), 'duration_s': duration})
            for (chat_id,) in rows:
                send_menu(chat_id, None, "🔔 Напоминание! Время работать над привычками!")
    except Exception as e:
        logger.exception("send_menu_for_tz failed", extra={'timezone': timezone, 'error': str(e)})
        notify_admin_safe(f"⚠ Ошибка напоминаний для {timezone}")
    finally:
        put_conn(conn)

if SCHEDULER_LEADER == '1':
    for tz in ['Europe/Moscow', 'Europe/London', 'America/New_York', 'Asia/Tokyo', 'UTC']:
        scheduler.add_job(
            lambda tz=tz: send_menu_for_tz(tz),
            CronTrigger(hour=hour, minute=minute, timezone=tz)
        )
        logger.info("Scheduled reminders for timezone", extra={'timezone': tz})
    scheduler.start()
    logger.info("Scheduler started", extra={'leader': SCHEDULER_LEADER})
else:
    logger.info("Scheduler not started in this process", extra={'leader': SCHEDULER_LEADER})

# -----------------------
# Webhook helpers
# -----------------------
def ensure_webhook(max_retries=3, delay=3):
    if not WEBHOOK_URL:
        logger.warning("WEBHOOK_URL not configured; skipping ensure_webhook")
        return False
    for attempt in range(1, max_retries + 1):
        start_time = pendulum.now('UTC')
        try:
            resp = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo", timeout=10).json()
            duration = (pendulum.now('UTC') - start_time).total_seconds()
            logger.debug("getWebhookInfo response", extra={'response': resp, 'duration_s': duration})
            if resp.get("ok"):
                info = resp["result"]
                url = info.get("url")
                pending = info.get("pending_update_count", 0)
                if not url:
                    logger.warning("Webhook not set, setting now", extra={'attempt': attempt, 'max_retries': max_retries, 'url': WEBHOOK_URL})
                    res = bot.set_webhook(url=WEBHOOK_URL)
                    logger.info("set_webhook result", extra={'success': res, 'url': WEBHOOK_URL, 'duration_s': (pendulum.now('UTC') - start_time).total_seconds()})
                    if not res:
                        logger.error("set_webhook returned False")
                    sleep(delay)
                else:
                    logger.info("Webhook active", extra={'url': url, 'pending_updates': pending, 'duration_s': duration})
                    if pending and ADMIN_ID:
                        notify_admin_safe(f"⚠ Внимание: в очереди Telegram осталось {pending} апдейтов.")
                    return True
            else:
                logger.error("getWebhookInfo returned not ok", extra={'response': resp, 'duration_s': duration})
                notify_admin_safe(f"❌ Ошибка getWebhookInfo: {json.dumps(resp, ensure_ascii=False)}")
        except Exception as e:
            logger.exception("ensure_webhook exception", extra={'attempt': attempt, 'error': str(e), 'duration_s': (pendulum.now('UTC') - start_time).total_seconds()})
            sleep(delay)
    logger.error("Webhook did not become active after retries")
    notify_admin_safe("❌ Вебхук так и не установился после нескольких попыток.")
    return False

def setup_webhook_in_thread():
    def _setup():
        sleep(1)  # Задержка для гарантии готовности процесса
        try:
            logger.info("Removing existing webhook", extra={'drop_pending_updates': True})
            bot.remove_webhook(drop_pending_updates=True)
            sleep(1)
        except Exception as e:
            logger.exception("remove_webhook warning (non-fatal)", extra={'error': str(e)})
        if WEBHOOK_URL:
            try:
                success = bot.set_webhook(url=WEBHOOK_URL)
                logger.info("Webhook setup attempted", extra={'success': success, 'url': WEBHOOK_URL})
                if success:
                    ensure_webhook(max_retries=5, delay=2)
                    def periodic_check():
                        while True:
                            ensure_webhook(max_retries=2, delay=1)
                            sleep(60 * WEBHOOK_CHECK_INTERVAL)
                    t = threading.Thread(target=periodic_check, daemon=True, name="webhook-check")
                    t.start()
                    logger.info("Periodic webhook check started", extra={'interval_min': WEBHOOK_CHECK_INTERVAL})
                else:
                    logger.error("bot.set_webhook returned False")
                    notify_admin_safe("❌ Не удалось установить вебхук (set_webhook вернул False)")
            except Exception as e:
                logger.exception("Exception while setting webhook", extra={'error': str(e)})
                notify_admin_safe("⚠ Ошибка установки webhook")
        else:
            logger.warning("WEBHOOK_URL not provided; webhook disabled")
    thr = threading.Thread(target=_setup, daemon=True, name="webhook-setup-thread")
    thr.start()
    logger.info("Webhook setup thread started")

# -----------------------
# Flask routes
# -----------------------
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        raw = request.get_data().decode('utf-8')
        if not raw:
            logger.warning("Empty webhook payload received", extra={'headers': dict(request.headers)})
            return '', 400
        logger.debug("Webhook received", extra={'payload': raw[:1000], 'headers': dict(request.headers)})
        update = types.Update.de_json(raw)
        if not update:
            logger.error("Failed to parse webhook update", extra={'raw': raw[:1000]})
            return '', 400
        logger.info("Processing update", extra={'update_id': update.update_id, 'chat_id': getattr(update.message or update.callback_query, 'chat', {}).get('id')})
        bot.process_new_updates([update])
        return '', 200
    except Exception as e:
        logger.exception("Error processing webhook", extra={'raw': raw[:1000] if 'raw' in locals() else None, 'headers': dict(request.headers), 'error': str(e)})
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
# Инициализация при импорте
# -----------------------
try:
    init_db()
except Exception as e:
    logger.exception("Fatal DB init error — aborting import", extra={'error': str(e)})
    raise

if WEBHOOK_WORKER == '1':
    logger.info("WEBHOOK_WORKER=1 -> starting webhook setup thread")
    setup_webhook_in_thread()
else:
    logger.info("WEBHOOK_WORKER!=1 -> skipping webhook setup in this process")

if SCHEDULER_LEADER == '1':
    logger.info("SCHEDULER_LEADER=1 -> scheduler already started above")
else:
    logger.info("SCHEDULER_LEADER!=1 -> scheduler not controlled by this process")

logger.info("Bot module imported, app ready", extra={'pid': os.getpid()})
