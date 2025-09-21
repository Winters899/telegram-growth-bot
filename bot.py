import os
import logging
import random
from time import sleep
from flask import Flask, request
from telebot import TeleBot, types
from telebot.util import escape_markdown
import psycopg2
import pendulum
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from threading import Lock

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# Инициализация переменных окружения
BOT_TOKEN = os.getenv('BOT_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
RENDER_EXTERNAL_HOSTNAME = os.getenv('RENDER_EXTERNAL_HOSTNAME')
ADMIN_ID = int(os.getenv('TELEGRAM_ADMIN_ID', '0') or 0)
DEFAULT_TIMEZONE = os.getenv('BOT_TIMEZONE', 'UTC')
REMINDER_HOUR = os.getenv('REMINDER_HOUR', '09:00')

WEBHOOK_URL = f'https://{RENDER_EXTERNAL_HOSTNAME}/webhook' if RENDER_EXTERNAL_HOSTNAME else None
if not BOT_TOKEN or not DATABASE_URL:
    logging.error('Ошибка запуска: BOT_TOKEN или DATABASE_URL должны быть установлены')
    raise SystemExit(1)

# Инициализация бота и Flask
bot = TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Инициализация RateLimiter
class RateLimiter:
    def __init__(self, max_calls=60, period=60):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
        self.lock = Lock()

    def __enter__(self):
        with self.lock:
            now = pendulum.now().timestamp()
            # keep only recent calls
            self.calls = [t for t in self.calls if now - t < self.period]
            if len(self.calls) >= self.max_calls:
                wait = self.period - (now - self.calls[0])
                if wait > 0:
                    sleep(wait)
            self.calls.append(now)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

rate_limiter = RateLimiter(max_calls=60, period=60)  # 60 сообщений в минуту

# Безопасное уведомление админа (без ретраев)
def notify_admin_safe(text):
    if ADMIN_ID:
        try:
            bot.send_message(ADMIN_ID, text)
        except Exception as e:
            logging.error(f"Не удалось уведомить админа: {e}")

# Отправка сообщений с учётом rate limiting и ретраев
def send_message_with_rate_limit(chat_id, text, **kwargs):
    logging.info(f"Attempting to send message to chat_id={chat_id}, text={text[:50]}...")
    with rate_limiter:
        last_exc = None
        for attempt in range(5):
            try:
                msg = bot.send_message(chat_id, text, **kwargs)
                logging.info(f"Message sent to chat_id={chat_id}, message_id={getattr(msg, 'message_id', None)}")
                return msg
            except Exception as e:
                last_exc = e
                logging.warning(f"Attempt {attempt+1}/5 failed for chat_id={chat_id}: {e}")
                sleep(2 ** attempt)
        logging.error(f"Failed to send message to chat_id={chat_id} after 5 attempts: {last_exc}")
        if chat_id != ADMIN_ID:
            notify_admin_safe(f"⚠ Ошибка отправки сообщения для {chat_id}: {str(last_exc)[:200]}")
        return None

# Инициализация базы данных
def init_db():
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
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
        logging.info('Схема базы данных инициализирована с индексами.')
    except Exception as e:
        logging.error(f'Ошибка инициализации базы данных: {e}')
        notify_admin_safe(f'⚠ Ошибка инициализации базы данных: {e}')
        raise

# Функции базы данных
def get_user(chat_id):
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT chat_id, username, timezone, subscribed, last_menu_message_id FROM users WHERE chat_id = %s', (chat_id,))
                user = cur.fetchone()
        if user:
            return {
                'chat_id': user[0],
                'username': user[1],
                'timezone': user[2],
                'subscribed': user[3],
                'last_menu_message_id': user[4]
            }
        return None
    except Exception as e:
        logging.error(f'Ошибка get_user для chat_id={chat_id}: {e}')
        return None

def update_user(chat_id, **kwargs):
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                if kwargs:
                    fields = ', '.join(f'{k} = %s' for k in kwargs)
                    values = list(kwargs.values()) + [chat_id]
                    cur.execute(f'UPDATE users SET {fields} WHERE chat_id = %s', values)
                # if no rows updated -> insert (idempotent insert)
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
        logging.info(f'Пользователь chat_id={chat_id} обновлён/создан')
    except Exception as e:
        logging.error(f'Ошибка update_user для chat_id={chat_id}: {e}')

def add_task(chat_id, task_date, completed=False):
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute('INSERT INTO tasks (chat_id, task_date, completed) VALUES (%s, %s, %s)', (chat_id, task_date, completed))
            conn.commit()
        logging.info(f'Задача добавлена для chat_id={chat_id}, task_date={task_date}')
    except Exception as e:
        logging.error(f'Ошибка add_task для chat_id={chat_id}: {e}')

def get_tasks(chat_id, start_date, end_date):
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT task_date, completed FROM tasks WHERE chat_id = %s AND task_date BETWEEN %s AND %s ORDER BY task_date', (chat_id, start_date, end_date))
                tasks = cur.fetchall()
        return [{'task_date': t[0], 'completed': t[1]} for t in tasks]
    except Exception as e:
        logging.error(f'Ошибка get_tasks для chat_id={chat_id}: {e}')
        return []

# Мотивационные цитаты
MOTIVATIONAL_QUOTES = [
    "Каждый день — новый шанс стать лучше!",
    "Маленькие шаги приводят к большим целям!",
    "Ты сильнее, чем думаешь!",
]

# Клавиатура
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

# Безопасная отправка/обновление меню
def send_menu(chat_id, user, text):
    logging.info(f"Sending menu to chat_id={chat_id}, text={text[:50]}...")
    try:
        fresh_user = get_user(chat_id) or user or {'subscribed': False, 'timezone': DEFAULT_TIMEZONE}
        prev_id = fresh_user.get('last_menu_message_id')
        username = f"@{fresh_user.get('username')}" if fresh_user.get('username') else "друг"
        motivation = random.choice(MOTIVATIONAL_QUOTES)

        # Экранируем текст для MarkdownV2
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
                logging.info(f"Menu updated for chat_id={chat_id}, message_id={prev_id}")
                return
            except Exception as e:
                logging.warning(f"Failed to update menu message_id={prev_id} for chat_id={chat_id}: {e}")
                update_user(chat_id, last_menu_message_id=None)

        msg = send_message_with_rate_limit(
            chat_id,
            formatted_text,
            parse_mode="MarkdownV2",
            reply_markup=get_inline_keyboard(fresh_user)
        )
        if msg:
            update_user(chat_id, last_menu_message_id=msg.message_id)
            logging.info(f"Menu sent for chat_id={chat_id}, message_id={msg.message_id}")
        else:
            logging.error(f"Failed to send menu to chat_id={chat_id}: No message returned")
    except Exception as e:
        logging.error(f"Error in send_menu for chat_id={chat_id}: {e}")
        try:
            send_message_with_rate_limit(chat_id, escape_markdown("⚠ Что-то пошло не так. Попробуй позже!", version=2), parse_mode="MarkdownV2")
        except Exception:
            notify_admin_safe(f"⚠ Ошибка send_menu для {chat_id}: {e}")

# Обработчики команд и callback'ов
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    username = message.from_user.username or "друг"
    logging.info(f"Processing /start for chat_id={chat_id}, username=@{username}")
    update_user(chat_id, username=username)
    safe_username = escape_markdown(username, version=2)
    send_menu(chat_id, None, f"Привет, @{safe_username}! 👋 Я твой наставник по привычкам.")

@bot.message_handler(commands=['stats'])
def stats(message):
    chat_id = message.chat.id
    logging.info(f"Processing /stats for chat_id={chat_id}")
    user = get_user(chat_id)
    if not user:
        send_message_with_rate_limit(chat_id, escape_markdown("⚠ Сначала начни с /start", version=2), parse_mode="MarkdownV2")
        return
    start_date = pendulum.now(user['timezone']).subtract(weeks=1).date()
    end_date = pendulum.now(user['timezone']).date()
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
        logging.warning(f"Unauthorized /all_stats attempt by chat_id={chat_id}")
        return
    logging.info("Processing /all_stats")
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
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
    except Exception as e:
        logging.error(f"Ошибка all_stats: {e}")
        notify_admin_safe(f"⚠ Ошибка all_stats: {e}")
        send_message_with_rate_limit(chat_id, escape_markdown(f"⚠ Ошибка: {e}", version=2), parse_mode="MarkdownV2")

@bot.message_handler(commands=['settimezone'])
def set_timezone(message):
    chat_id = message.chat.id
    logging.info(f"Processing /settimezone for chat_id={chat_id}")
    user = get_user(chat_id)
    if not user:
        send_message_with_rate_limit(chat_id, escape_markdown("⚠ Сначала начни с /start", version=2), parse_mode="MarkdownV2")
        return
    keyboard = types.InlineKeyboardMarkup()
    timezones = ['Europe/Moscow', 'Europe/London', 'America/New_York', 'Asia/Tokyo', 'UTC']
    for tz in timezones:
        keyboard.add(types.InlineKeyboardButton(tz, callback_data=f'tz_{tz}'))
    send_message_with_rate_limit(chat_id, escape_markdown("🌍 Выбери часовой пояс:", version=2), parse_mode="MarkdownV2", reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    chat_id = call.message.chat.id
    user = get_user(chat_id)
    if not user:
        send_message_with_rate_limit(chat_id, escape_markdown("⚠ Сначала начни с /start", version=2), parse_mode="MarkdownV2")
        return
    logging.info(f"Processing callback {call.data} for chat_id={chat_id}")
    if call.data == 'today':
        task_date = pendulum.now(user['timezone']).date()
        add_task(chat_id, task_date, completed=True)
        send_menu(chat_id, user, "✅ Отлично, ты сделал это сегодня!")
    elif call.data == 'next':
        send_menu(chat_id, user, "📅 Планируй следующий день!")
    elif call.data == 'subscribe':
        update_user(chat_id, subscribed=not user['subscribed'])
        send_menu(chat_id, user, "🔔 Подписка обновлена!")
    elif call.data == 'stats':
        start_date = pendulum.now(user['timezone']).subtract(weeks=1).date()
        end_date = pendulum.now(user['timezone']).date()
        tasks = get_tasks(chat_id, start_date, end_date)
        completed = sum(1 for t in tasks if t['completed'])
        total = len(tasks)
        percentage = (completed / total * 100) if total > 0 else 0
        send_menu(chat_id, user, f"📊 Статистика за неделю:\n✅ Выполнено: {completed}/{total} ({percentage:.1f}%)")
    elif call.data.startswith('tz_'):
        timezone = call.data[3:]
        update_user(chat_id, timezone=timezone)
        send_menu(chat_id, user, f"🌍 Часовой пояс установлен: {timezone}")

# Напоминания
def send_scheduled_task_for_tz(timezone):
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT chat_id FROM users WHERE subscribed = TRUE AND timezone = %s', (timezone,))
                users = cur.fetchall()
        now = pendulum.now(timezone)
        for (chat_id,) in users:
            logging.info(f"Sending reminder to chat_id={chat_id}, timezone={timezone}")
            send_menu(chat_id, None, "🔔 Напоминание! Время работать над привычками!")
    except Exception as e:
        logging.error(f"Ошибка в send_scheduled_task_for_tz для {timezone}: {e}")
        notify_admin_safe(f"⚠ Ошибка напоминаний для {timezone}: {e}")

def cleanup_inactive_users():
    try:
        cutoff = pendulum.now('UTC').subtract(months=1)
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM users WHERE created_at < %s AND subscribed = FALSE', (cutoff,))
            conn.commit()
        logging.info("Неактивные пользователи удалены")
    except Exception as e:
        logging.error(f"Ошибка cleanup_inactive_users: {e}")
        notify_admin_safe(f"⚠ Ошибка очистки пользователей: {e}")

# Планировщик задач
scheduler = BackgroundScheduler()
try:
    hour, minute = map(int, REMINDER_HOUR.split(':'))
except Exception:
    hour, minute = 9, 0
scheduler.add_job(cleanup_inactive_users, 'cron', hour=0, minute=0, timezone='UTC')
for tz in ['Europe/Moscow', 'Europe/London', 'America/New_York', 'Asia/Tokyo', 'UTC']:
    scheduler.add_job(
        send_scheduled_task_for_tz,
        CronTrigger(hour=hour, minute=minute, timezone=tz),
        args=[tz]
    )
scheduler.start()

# Webhook endpoint
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = types.Update.de_json(request.get_json())
        if update:
            bot.process_new_updates([update])
            return '', 200
        logging.warning("Получен пустой update")
        return '', 400
    except Exception as e:
        logging.error(f"Ошибка обработки webhook: {e}")
        return '', 500

@app.route('/')
def index():
    return 'Привет, я жив!'

# Инициализация и запуск
if __name__ == '__main__':
    init_db()
    try:
        # настроим вебхук только если есть адрес
        try:
            bot.remove_webhook()
        except Exception:
            logging.info('Webhook removal skipped or failed (ok if not set).')
        if WEBHOOK_URL:
            try:
                bot.set_webhook(url=WEBHOOK_URL)
                logging.info(f"🔗 Вебхук установлен: {WEBHOOK_URL}")
            except Exception as e:
                logging.error(f"Ошибка установки webhook: {e}")
                notify_admin_safe(f"⚠ Ошибка установки webhook: {e}")
                raise
        else:
            logging.info('WEBHOOK_URL не настроен — бот может работать в polling режиме локально (не рекомендовано для Render).')
    except Exception as e:
        logging.error(f"Startup webhook error: {e}")
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
