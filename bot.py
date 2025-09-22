import os
import json
import logging
import time
import pendulum
from flask import Flask, request, jsonify
from telebot import TeleBot
from telebot.types import Update
from telebot.formatting import escape_markdown # Исправленный импорт
from telebot.apihelper import ApiTelegramException
import psycopg2
from psycopg2.pool import SimpleConnectionPool
import atexit
import requests

# -----------------------
# Конфигурация окружения
# -----------------------
BOT_TOKEN = os.getenv('BOT_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
RENDER_EXTERNAL_HOSTNAME = os.getenv('RENDER_EXTERNAL_HOSTNAME')
WEBHOOK_URL = os.getenv('WEBHOOK_URL') or (f'https://{RENDER_EXTERNAL_HOSTNAME}/webhook' if RENDER_EXTERNAL_HOSTNAME else None)
try:
    ADMIN_ID = int(os.getenv('TELEGRAM_ADMIN_ID', '0'))
except ValueError:
    logging.error("Invalid TELEGRAM_ADMIN_ID", extra={'value': os.getenv('TELEGRAM_ADMIN_ID')})
    ADMIN_ID = 0
WEBHOOK_WORKER = os.getenv('WEBHOOK_WORKER', '1')

if not BOT_TOKEN or not DATABASE_URL or not WEBHOOK_URL:
    logging.error("Missing required environment variables", extra={'BOT_TOKEN': bool(BOT_TOKEN), 'DATABASE_URL': bool(DATABASE_URL), 'WEBHOOK_URL': bool(WEBHOOK_URL)})
    raise RuntimeError('Не заданы BOT_TOKEN, DATABASE_URL или WEBHOOK_URL')

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
logger.info("Logger initialized")

# -----------------------
# Flask + TeleBot
# -----------------------
app = Flask(__name__)
bot = TeleBot(BOT_TOKEN)

# -----------------------
# DB connection pool
# -----------------------
db_pool = SimpleConnectionPool(1, 5, DATABASE_URL)

def get_conn():
    try:
        conn = db_pool.getconn()
        logger.debug("Acquired DB connection")
        return conn
    except Exception as e:
        logger.exception("get_conn error", extra={'error': str(e)})
        raise

def put_conn(conn):
    try:
        db_pool.putconn(conn)
        logger.debug("Returned DB connection")
    except Exception as e:
        logger.exception("put_conn error", extra={'error': str(e)})

@atexit.register
def close_pool():
    try:
        db_pool.closeall()
        logger.info("DB pool closed")
    except Exception:
        logger.exception("Error closing DB pool")

# -----------------------
# Инициализация БД
# -----------------------
def init_db():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    chat_id BIGINT PRIMARY KEY,
                    username TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
            conn.commit()
            logger.info("DB schema initialized")
    except Exception as e:
        logger.exception("init_db failed", extra={'error': str(e)})
        raise
    finally:
        put_conn(conn)

# -----------------------
# Отправка сообщений
# -----------------------
def notify_admin_safe(text):
    if ADMIN_ID:
        try:
            bot.send_message(ADMIN_ID, text)
            logger.info("Admin notified", extra={'admin_id': ADMIN_ID, 'text': text[:100]})
        except Exception as e:
            logger.exception("Failed to notify admin", extra={'admin_id': ADMIN_ID, 'error': str(e)})

def send_message_safe(chat_id, text, **kwargs):
    preview = text if len(text) < 100 else text[:100] + '...'
    logger.info("Sending message", extra={'chat_id': chat_id, 'text_preview': preview})
    try:
        msg = bot.send_message(chat_id, text, **kwargs)
        logger.info("Message sent", extra={'chat_id': chat_id, 'message_id': msg.message_id})
        return msg
    except ApiTelegramException as e:
        logger.error("Telegram API error", extra={'chat_id': chat_id, 'error_code': e.error_code, 'error': e.description})
        if e.error_code == 429:
            sleep_time = e.result_json.get('parameters', {}).get('retry_after', 1)
            logger.warning("Rate limit hit, sleeping", extra={'sleep_s': sleep_time})
            time.sleep(sleep_time)
            return None
        elif e.error_code == 403:
            logger.warning("Bot blocked by user", extra={'chat_id': chat_id})
            return None
        notify_admin_safe(f"⚠ Ошибка отправки для {chat_id}: {e.description}")
        return None
    except Exception as e:
        logger.exception("Send message failed", extra={'chat_id': chat_id, 'error': str(e)})
        notify_admin_safe(f"⚠ Ошибка отправки для {chat_id}: {str(e)[:100]}")
        return None

# -----------------------
# Операции с БД
# -----------------------
def get_user(chat_id, retries=3, delay=1):
    for attempt in range(retries):
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT chat_id, username FROM users WHERE chat_id = %s', (chat_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return {'chat_id': row[0], 'username': row[1]}
        except psycopg2.OperationalError as e:
            logger.warning(f"DB error, retrying {attempt+1}/{retries}", extra={'error': str(e)})
            time.sleep(delay)
        finally:
            put_conn(conn)
    logger.error("Failed to get user after retries", extra={'chat_id': chat_id})
    return None

def update_user(chat_id, username):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('''
                INSERT INTO users (chat_id, username)
                VALUES (%s, %s)
                ON CONFLICT (chat_id) DO UPDATE SET username = EXCLUDED.username
            ''', (chat_id, username))
            conn.commit()
            logger.info("User updated/created", extra={'chat_id': chat_id, 'username': username})
    except Exception as e:
        logger.exception("update_user failed", extra={'chat_id': chat_id, 'error': str(e)})
    finally:
        put_conn(conn)

# -----------------------
# Команды
# -----------------------
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    username = message.from_user.username or "друг"
    if not username.isalnum():
        username = "друг"
    logger.info("/start command", extra={'chat_id': chat_id, 'username': username})
    update_user(chat_id, username)
    safe_username = escape_markdown(username, version=2)
    send_message_safe(chat_id, f"Привет, @{safe_username}! 👋 Я твой бот.", parse_mode="MarkdownV2")

@bot.message_handler(commands=['stats'])
def stats(message):
    chat_id = message.chat.id
    logger.info("/stats command", extra={'chat_id': chat_id})
    user = get_user(chat_id)
    if not user:
        send_message_safe(chat_id, escape_markdown("⚠ Сначала начни с /start", version=2), parse_mode="MarkdownV2")
        return
    safe_username = escape_markdown(user['username'] or "друг", version=2)
    send_message_safe(chat_id, f"📊 Статистика для @{safe_username}: Пока нет данных!", parse_mode="MarkdownV2")

@bot.message_handler(commands=['help'])
def help_command(message):
    chat_id = message.chat.id
    logger.info("/help command", extra={'chat_id': chat_id})
    send_message_safe(chat_id, escape_markdown(
        "Доступные команды:\n/start - Начать работу с ботом\n/stats - Показать статистику\n/help - Показать это сообщение",
        version=2), parse_mode="MarkdownV2")

# -----------------------
# Webhook
# -----------------------
def setup_webhook():
    try:
        bot.remove_webhook(drop_pending_updates=True)
        success = bot.set_webhook(url=WEBHOOK_URL)
        logger.info("Webhook setup", extra={'success': success, 'url': WEBHOOK_URL})
        if not success:
            notify_admin_safe("❌ Не удалось установить вебхук")
    except Exception as e:
        logger.exception("Webhook setup failed", extra={'error': str(e)})
        notify_admin_safe(f"⚠ Ошибка установки вебхука: {str(e)[:100]}")

# -----------------------
# Flask routes
# -----------------------
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        raw = request.get_data().decode('utf-8')
        if not raw:
            logger.warning("Empty webhook payload", extra={'headers': dict(request.headers)})
            return '', 400
        logger.debug("Webhook received", extra={'payload': raw[:500]})
        update = Update.de_json(json.loads(raw))
        if not update:
            logger.error("Failed to parse webhook update", extra={'raw': raw[:500]})
            return '', 400
        chat_id = None
if update.message:
    chat_id = update.message.chat.id
elif update.callback_query:
    chat_id = update.callback_query.message.chat.id

logger.info("Processing update", extra={'update_id': update.update_id, 'chat_id': chat_id})

        bot.process_new_updates([update])
        return '', 200
    except Exception as e:
        logger.exception("Webhook error", extra={'error': str(e)})
        return '', 500

@app.route('/')
def index():
    return jsonify({'status': 'ok', 'pid': os.getpid(), 'webhook_url': WEBHOOK_URL})

@app.route('/webhook_status')
def webhook_status():
    try:
        info = bot.get_webhook_info()
        return jsonify({'status': 'ok', 'webhook_info': info.to_dict()})
    except Exception as e:
        logger.exception("Webhook status check failed", extra={'error': str(e)})
        return jsonify({'status': 'error', 'error': str(e)}), 500

# -----------------------
# Инициализация
# -----------------------
try:
    init_db()
    if WEBHOOK_WORKER == '1':
        setup_webhook()
    logger.info("Bot started", extra={'pid': os.getpid()})
except Exception as e:
    logger.exception("Fatal DB init error", extra={'error': str(e)})
    notify_admin_safe(f"⚠ Bot failed to start: {str(e)[:100]}")
    raise

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

