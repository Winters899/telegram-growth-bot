import os
import logging
import random
from time import sleep
from flask import Flask, request
from telebot import TeleBot, types
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
ADMIN_ID = int(os.getenv('TELEGRAM_ADMIN_ID', '0'))
DEFAULT_TIMEZONE = os.getenv('BOT_TIMEZONE', 'UTC')
REMINDER_HOUR = os.getenv('REMINDER_HOUR', '09:00')

WEBHOOK_URL = f'https://{RENDER_EXTERNAL_HOSTNAME}/webhook'
if not BOT_TOKEN or not DATABASE_URL or not RENDER_EXTERNAL_HOSTNAME:
    logging.error('Ошибка запуска: BOT_TOKEN, DATABASE_URL или RENDER_EXTERNAL_HOSTNAME должны быть установлены')
    exit(1)

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
            self.calls = [t for t in self.calls if now - t < self.period]
            if len(self.calls) >= self.max_calls:
                sleep(self.period - (now - self.calls[0]))
            self.calls.append(now)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

rate_limiter = RateLimiter(max_calls=60, period=60)  # 60 сообщений в минуту

# Инициализация базы данных
def init_db():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    chat_id BIGINT PRIMARY KEY,
                    username TEXT,
                    timezone TEXT DEFAULT %s,
                    subscribed BOOLEAN DEFAULT FALSE,
                    last_menu_message_id BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''', (DEFAULT_TIMEZONE,))
            cur.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT,
                    task_date DATE,
                    completed BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (chat_id) REFERENCES users (chat_id)
                )
            ''')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_tasks_chat_id_date ON tasks (chat_id, task_date)')
        conn.commit()
        logging.info('Схема базы данных инициализирована с индексами.')
        conn.close()
    except Exception as e:
        logging.error(f'Ошибка инициализации базы данных: {e}')
        send_message_with_rate_limit(ADMIN_ID, f'⚠ Ошибка инициализации базы данных: {e}')
        exit(1)

# Функции базы данных
def get_user(chat_id):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            cur.execute('SELECT chat_id, username, timezone, subscribed, last_menu_message_id FROM users WHERE chat_id = %s', (chat_id,))
            user = cur.fetchone()
        conn.close()
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
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            fields = ', '.join(f'{k} = %s' for k in kwargs)
            values = list(kwargs.values()) + [chat_id]
            cur.execute(f'UPDATE users SET {fields} WHERE chat_id = %s', values)
            if cur.rowcount == 0:
                cur.execute(
                    'INSERT INTO users (chat_id, username, timezone, subscribed, last_menu_message_id) VALUES (%s, %s, %s, %s, %s)',
                    (chat_id, kwargs.get('username'), kwargs.get('timezone', DEFAULT_TIMEZONE), kwargs.get('subscribed', False), kwargs.get('last_menu_message_id'))
                )
        conn.commit()
        conn.close()
        logging.info(f'Пользователь chat_id={chat_id} обновлён/создан')
    except Exception as e:
        logging.error(f'Ошибка update_user для chat_id={chat_id}: {e}')

def add_task(chat_id, task_date, completed=False):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            cur.execute('INSERT INTO tasks (chat_id, task_date, completed) VALUES (%s, %s, %s)', (chat_id, task_date, completed))
        conn.commit()
        conn.close()
        logging.info(f'Задача добавлена для chat_id={chat_id}, task_date={task_date}')
    except Exception as e:
        logging.error(f'Ошибка add_task для chat_id={chat_id}: {e}')

def get_tasks(chat_id, start_date, end_date):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            cur.execute('SELECT task_date, completed FROM tasks WHERE chat_id = %s AND task_date BETWEEN %s AND %s ORDER BY task_date', (chat_id, start_date, end_date))
            tasks = cur.fetchall()
        conn.close()
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

# Отправка сообщений с учётом rate limiting
def send_message_with_rate_limit(chat_id, text, **kwargs):
    logging.info(f"Attempting to send message to chat_id={chat_id}, text={text[:50]}...")
    with rate_limiter:
        for attempt in range(5):
            try:
                msg = bot.send_message(chat_id, text, **kwargs)
                logging.info(f"Message sent to chat_id={chat_id}, message_id={msg.message_id}")
                return msg
            except Exception as e:
                logging.warning(f"Attempt {attempt+1}/5: Failed to send message to chat_id={chat_id}: {e}")
                sleep(2 ** attempt)
        logging.error(f"Failed to send message to chat_id={chat_id} after 5 attempts")
        if chat_id != ADMIN_ID:
            send_message_with_rate_limit(ADMIN_ID, f"⚠ Ошибка отправки сообщения для {chat_id}: {e}")
        return None

# Создание клавиатуры
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

# Отправка меню
def send_menu(chat_id, user, text):
    logging.info(f"Sending menu to chat_id={chat_id}, text={text[:50]}...")
    try:
        fresh_user = get_user(chat_id) or user or {'subscribed': False, 'timezone': DEFAULT_TIMEZONE}
        prev_id = fresh_user.get('last_menu_message_id')
        username = f"@{fresh_user.get('username')}" if fresh_user.get('username') else "друг"
        motivation = random.choice(MOTIVATIONAL_QUOTES)
        formatted_text = f"**{text}**\n\n_{motivation}_"
        if prev_id:
            try:
                bot.edit_message_text(
                    formatted_text,
                    chat_id,
                    prev_id,
                    parse_mode="Markdown",
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
            parse_mode="Markdown",
            reply_markup=get_inline_keyboard(fresh_user)
        )
        if msg:
            update_user(chat_id, last_menu_message_id=msg.message_id)
            logging.info(f"Menu sent for chat_id={chat_id}, message_id={msg.message_id}")
        else:
            logging.error(f"Failed to send menu to chat_id={chat_id}: No message returned")
            raise Exception("Failed to send menu")
    except Exception as e:
        logging.error(f"Error in send_menu for chat_id={chat_id}: {e}")
        send_message_with_rate_limit(chat_id, "⚠ Что-то пошло не так. Попробуй позже!")
        if chat_id != ADMIN_ID:
            send_message_with_rate_limit(ADMIN_ID, f"⚠ Ошибка send_menu для {chat_id}: {e}")

# Обработчики команд
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    username = message.from_user.username
    logging.info(f"Processing /start for chat_id={chat_id}, username=@{username}")
    update_user(chat_id, username=username)
    send_menu(chat_id, None, f"Привет, @{username}! 👋 Я твой наставник по привычкам.")

@bot.message_handler(commands=['stats'])
def stats(message):
    chat_id = message.chat.id
    logging.info(f"Processing /stats for chat_id={chat_id}")
    user = get_user(chat_id)
    if not user:
        send_message_with_rate_limit(chat_id, "⚠ Сначала начни с /start")
        return
    start_date = pendulum.now(user['timezone']).subtract(weeks=1).date()
    end_date = pendulum.now(user['timezone']).date()
    tasks = get_tasks(chat_id, start_date, end_date)
    completed = sum(1 for t in tasks if t['completed'])
    total = len(tasks)
    percentage = (completed / total * 100) if total > 0 else 0
    send_message_with_rate_limit(chat_id, f"📊 Статистика за неделю:\n✅ Выполнено: {completed}/{total} ({percentage:.1f}%)")

@bot.message_handler(commands=['all_stats'])
def all_stats(message):
    chat_id = message.chat.id
    if chat_id != ADMIN_ID:
        logging.warning(f"Unauthorized /all_stats attempt by chat_id={chat_id}")
        return
    logging.info("Processing /all_stats")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM users WHERE subscribed = TRUE')
            subscribed = cur.fetchone()[0]
            cur.execute('SELECT COUNT(*) FROM tasks WHERE completed = TRUE')
            completed = cur.fetchone()[0]
            cur.execute('SELECT COUNT(*) FROM tasks')
            total = cur.fetchone()[0]
        conn.close()
        percentage = (completed / total * 100) if total > 0 else 0
        send_message_with_rate_limit(chat_id, f"📊 Общая статистика:\n👥 Подписчиков: {subscribed}\n✅ Выполнено задач: {completed}/{total} ({percentage:.1f}%)")
    except Exception as e:
        logging.error(f"Ошибка all_stats: {e}")
        send_message_with_rate_limit(chat_id, f"⚠ Ошибка: {e}")

@bot.message_handler(commands=['settimezone'])
def set_timezone(message):
    chat_id = message.chat.id
    logging.info(f"Processing /settimezone for chat_id={chat_id}")
    user = get_user(chat_id)
    if not user:
        send_message_with_rate_limit(chat_id, "⚠ Сначала начни с /start")
        return
    keyboard = types.InlineKeyboardMarkup()
    timezones = ['Europe/Moscow', 'Europe/London', 'America/New_York', 'Asia/Tokyo', 'UTC']
    for tz in timezones:
        keyboard.add(types.InlineKeyboardButton(tz, callback_data=f'tz_{tz}'))
    send_message_with_rate_limit(chat_id, "🌍 Выбери часовой пояс:", reply_markup=keyboard)

# Обработчики callback'ов
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    chat_id = call.message.chat.id
    user = get_user(chat_id)
    if not user:
        send_message_with_rate_limit(chat_id, "⚠ Сначала начни с /start")
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
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            cur.execute('SELECT chat_id FROM users WHERE subscribed = TRUE AND timezone = %s', (timezone,))
            users = cur.fetchall()
        conn.close()
        now = pendulum.now(timezone)
        for (chat_id,) in users:
            logging.info(f"Sending reminder to chat_id={chat_id}, timezone={timezone}")
            send_menu(chat_id, None, f"🔔 Напоминание! Время работать над привычками!")
    except Exception as e:
        logging.error(f"Ошибка в send_scheduled_task_for_tz для {timezone}: {e}")
        send_message_with_rate_limit(ADMIN_ID, f"⚠ Ошибка напоминаний для {timezone}: {e}")

def cleanup_inactive_users():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            cur.execute('DELETE FROM users WHERE created_at < %s AND subscribed = FALSE', (pendulum.now().subtract(months=1),))
        conn.commit()
        conn.close()
        logging.info("Неактивные пользователи удалены")
    except Exception as e:
        logging.error(f"Ошибка cleanup_inactive_users: {e}")
        send_message_with_rate_limit(ADMIN_ID, f"⚠ Ошибка очистки пользователей: {e}")

# Планировщик задач
scheduler = BackgroundScheduler()
hour, minute = map(int, REMINDER_HOUR.split(':'))
scheduler.add_job(cleanup_inactive_users, 'cron', hour=0, minute=0)
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
        bot.remove_webhook()
        bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"🔗 Вебхук установлен: {WEBHOOK_URL}")
    except Exception as e:
        logging.error(f"Ошибка установки webhook: {e}")
        send_message_with_rate_limit(ADMIN_ID, f"⚠ Ошибка установки webhook: {e}")
        exit(1)
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
