import os
import logging
from collections import deque
from time import monotonic
import atexit
import signal
from flask import Flask, request
from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
import pendulum
from apscheduler.schedulers.background import BackgroundScheduler

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

# Инициализация Flask и TeleBot
app = Flask(__name__)
bot = TeleBot(os.getenv("BOT_TOKEN"))
scheduler = BackgroundScheduler()

# Конфигурация
DEFAULT_TIMEZONE = os.getenv("BOT_TIMEZONE", "UTC")
REMINDER_HOUR = os.getenv("REMINDER_HOUR", "09:00")
ERROR_CHANNEL_ID = os.getenv("ERROR_CHANNEL_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

# Пул подключений к базе данных
db_pool = SimpleConnectionPool(1, 20, dsn=DATABASE_URL)

# Ограничитель скорости для сообщений
class RateLimiter:
    def __init__(self, max_calls, period):
        self.max_calls = max_calls
        self.period = period
        self.calls = deque()

    def __enter__(self):
        while len(self.calls) >= self.max_calls:
            if monotonic() - self.calls[0] > self.period:
                self.calls.popleft()
            else:
                logging.warning("Rate limit exceeded, waiting...")
                time.sleep(self.period - (monotonic() - self.calls[0]))
        self.calls.append(monotonic())
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

rate_limiter = RateLimiter(max_calls=50, period=60)

# Получение подключения к базе
def get_db():
    return db_pool.getconn()

def put_db(conn):
    db_pool.putconn(conn)

# Инициализация базы данных
def init_db():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    chat_id BIGINT PRIMARY KEY,
                    day INTEGER DEFAULT 1,
                    streak INTEGER DEFAULT 0,
                    last_done DATE,
                    achievements TEXT[] DEFAULT '{}',
                    subscribed BOOLEAN DEFAULT FALSE,
                    username TEXT,
                    last_menu_message_id INTEGER,
                    timezone TEXT DEFAULT %s,
                    last_reminder TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_users_subscribed ON users(subscribed);
                CREATE INDEX IF NOT EXISTS idx_users_last_done ON users(last_done);
            """, (DEFAULT_TIMEZONE,))
            conn.commit()
    logging.info("Схема базы данных инициализирована с индексами.")

# Получение пользователя
def get_user(chat_id):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE chat_id = %s", (chat_id,))
            user = cur.fetchone()
            if not user:
                cur.execute("""
                    INSERT INTO users (chat_id, day, streak, subscribed, timezone)
                    VALUES (%s, 1, 0, FALSE, %s)
                    RETURNING *
                """, (chat_id, DEFAULT_TIMEZONE))
                user = cur.fetchone()
                conn.commit()
            return user

# Отправка сообщения с ограничением скорости
def send_message_with_rate_limit(chat_id, text, **kwargs):
    with rate_limiter:
        for attempt in range(3):
            try:
                msg = bot.send_message(chat_id, text, **kwargs)
                logging.info(f"Message sent to chat_id={chat_id}")
                return msg
            except Exception as e:
                logging.warning(f"Повтор {attempt+1}/3: Ошибка отправки {chat_id}: {e}")
                time.sleep(2 ** attempt)
        logging.error(f"Не удалось отправить сообщение {chat_id}")
        send_error_to_channel(f"⚠ Ошибка отправки сообщения для {chat_id}: {e}")
        return None

# Отправка ошибок в канал
def send_error_to_channel(message):
    if ERROR_CHANNEL_ID:
        send_message_with_rate_limit(ERROR_CHANNEL_ID, message)
    else:
        logging.warning(f"Ошибка не отправлена в канал: {message}")

# Получение задачи для пользователя
def get_task(user):
    tasks = [
        "📝 Напиши список дел на сегодня",
        "🏃‍♂️ Сделай 10-минутную зарядку",
        # ... остальные задачи ...
    ]
    return tasks[(user['day'] - 1) % len(tasks)]

# Отправка меню
def send_menu(chat_id, user, text):
    try:
        keyboard = InlineKeyboardMarkup()
        keyboard.add(
            InlineKeyboardButton("✅ Выполнено", callback_data="done"),
            InlineKeyboardButton("📊 Статистика", callback_data="stats")
        )
        keyboard.add(InlineKeyboardButton("🕒 Установить часовой пояс", callback_data="set_timezone"))
        if user['last_menu_message_id']:
            try:
                bot.edit_message_text(text, chat_id, user['last_menu_message_id'], reply_markup=keyboard, parse_mode="Markdown")
                logging.info(f"Menu updated for chat_id={chat_id}")
                return
            except Exception as e:
                logging.warning(f"Не удалось обновить меню для {chat_id}: {e}")
        msg = send_message_with_rate_limit(chat_id, text, reply_markup=keyboard, parse_mode="Markdown")
        if msg:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE users SET last_menu_message_id = %s WHERE chat_id = %s", (msg.message_id, chat_id))
                    conn.commit()
            logging.info(f"Menu sent for chat_id={chat_id}")
    except Exception as e:
        logging.error(f"Error in send_menu for chat_id={chat_id}: {e}")
        send_error_to_channel(f"⚠ Ошибка в send_menu для {chat_id}: {e}")

# Обработчик команды /start
@bot.message_handler(commands=['start'])
def start(message):
    logging.info(f"Processing /start for chat_id={message.chat.id}")
    try:
        user = get_user(message.chat.id)
        user['username'] = message.from_user.username
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET username = %s WHERE chat_id = %s", (user['username'], message.chat.id))
                conn.commit()
        username = f"@{user['username']}" if user['username'] else "друг"
        send_menu(
            message.chat.id,
            user,
            f"👋 Привет, {username}! Добро пожаловать в 30-дневный челлендж продуктивности!\n\n📌 Сегодня: {get_task(user)}"
        )
        logging.info(f"Menu sent for chat_id={message.chat.id}")
    except Exception as e:
        logging.error(f"Error in /start for chat_id={message.chat.id}: {e}")
        send_error_to_channel(f"⚠ Ошибка в /start для {message.chat.id}: {e}")

# Обработчик команды /stats
@bot.message_handler(commands=['stats'])
def stats(message):
    logging.info(f"Processing /stats for chat_id={message.chat.id}")
    try:
        user = get_user(message.chat.id)
        text = (
            f"📊 *Статистика*\n\n"
            f"День: {user['day']}\n"
            f"Стрик: {user['streak']}\n"
            f"Достижения: {', '.join(user['achievements']) or 'Пока нет'}\n"
            f"Часовой пояс: {user['timezone']}"
        )
        send_menu(message.chat.id, user, text)
        logging.info(f"Stats sent for chat_id={message.chat.id}")
    except Exception as e:
        logging.error(f"Error in /stats for chat_id={message.chat.id}: {e}")
        send_error_to_channel(f"⚠ Ошибка в /stats для {message.chat.id}: {e}")

# Обработчик inline-кнопок
@bot.callback_query_handler(func=lambda call: True)
def handle_inline_buttons(call):
    logging.info(f"Processing callback {call.data} for chat_id={call.message.chat.id}")
    try:
        user = get_user(call.message.chat.id)
        if call.data == "done":
            today = pendulum.today(user['timezone']).date()
            if user['last_done'] != today:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE users
                            SET day = day + 1,
                                streak = CASE
                                    WHEN last_done = %s THEN streak + 1
                                    ELSE 1
                                END,
                                last_done = %s
                            WHERE chat_id = %s
                        """, (pendulum.yesterday(user['timezone']).date(), today, call.message.chat.id))
                        conn.commit()
                user = get_user(call.message.chat.id)
                if user['day'] in [7, 14, 21, 30]:
                    new_achievement = f"День {user['day']}"
                    with get_db() as conn:
                        with conn.cursor() as cur:
                            cur.execute("UPDATE users SET achievements = achievements || %s WHERE chat_id = %s", ([new_achievement], call.message.chat.id))
                            conn.commit()
                send_menu(call.message.chat.id, user, f"✅ Задание выполнено!\n\n📌 Следующее: {get_task(user)}")
            else:
                send_menu(call.message.chat.id, user, f"⏳ Ты уже выполнил задание сегодня!\n\n📌 Следующее: {get_task(user)}")
        elif call.data == "stats":
            stats(call.message)
        elif call.data == "set_timezone":
            send_message_with_rate_limit(call.message.chat.id, "🕒 Введи свой часовой пояс (например, Europe/Moscow):")
            bot.register_next_step_handler(call.message, set_timezone)
        logging.info(f"Callback {call.data} processed for chat_id={call.message.chat.id}")
    except Exception as e:
        logging.error(f"Error in callback {call.data} for chat_id={call.message.chat.id}: {e}")
        send_error_to_channel(f"⚠ Ошибка в callback {call.data} для {call.message.chat.id}: {e}")

# Установка часового пояса
def set_timezone(message):
    logging.info(f"Processing set_timezone for chat_id={message.chat.id}")
    try:
        timezone = message.text.strip()
        pendulum.timezone(timezone)  # Проверка валидности
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET timezone = %s WHERE chat_id = %s", (timezone, message.chat.id))
                conn.commit()
        user = get_user(message.chat.id)
        send_menu(message.chat.id, user, f"🕒 Часовой пояс установлен: {timezone}\n\n📌 Сегодня: {get_task(user)}")
        logging.info(f"Timezone set for chat_id={message.chat.id}")
    except Exception as e:
        logging.error(f"Error in set_timezone for chat_id={message.chat.id}: {e}")
        send_message_with_rate_limit(message.chat.id, "❌ Неверный часовой пояс. Попробуй снова (например, Europe/Moscow).")
        send_error_to_channel(f"⚠ Ошибка в set_timezone для {message.chat.id}: {e}")

# Очистка неактивных пользователей
def cleanup_inactive_users():
    logging.info("Starting cleanup_inactive_users")
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM users
                    WHERE subscribed = FALSE
                    AND last_done < %s
                """, (pendulum.now().subtract(weeks=1).date(),))
                deleted = cur.rowcount
                conn.commit()
        logging.info(f"Cleaned up {deleted} inactive users")
        if deleted > 0 and ERROR_CHANNEL_ID:
            send_message_with_rate_limit(ERROR_CHANNEL_ID, f"🧹 Удалено {deleted} неактивных пользователей")
    except Exception as e:
        logging.error(f"Error in cleanup_inactive_users: {e}")
        send_error_to_channel(f"⚠ Ошибка в cleanup_inactive_users: {e}")

# Отправка напоминаний
def send_scheduled_task_for_tz():
    logging.info("Starting send_scheduled_task_for_tz")
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE subscribed = TRUE")
                users = cur.fetchall()
        now = pendulum.now()
        for user in users:
            tz = pendulum.timezone(user['timezone'])
            local_time = now.in_timezone(tz)
            reminder_time = pendulum.parse(REMINDER_HOUR, tz=tz).time()
            if local_time.time() >= reminder_time and (not user['last_reminder'] or local_time.date() > pendulum.instance(user['last_reminder']).date()):
                send_menu(user['chat_id'], user, f"⏰ Напоминание! Сегодня: {get_task(user)}")
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE users SET last_reminder = %s WHERE chat_id = %s", (now, user['chat_id']))
                        conn.commit()
                logging.info(f"Reminder sent to chat_id={user['chat_id']}")
    except Exception as e:
        logging.error(f"Error in send_scheduled_task_for_tz: {e}")
        send_error_to_channel(f"⚠ Ошибка в send_scheduled_task_for_tz: {e}")

# Webhook маршрут
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = bot.process_new_updates([telebot.types.Update.de_json(request.get_json())])
        return '', 200
    except Exception as e:
        logging.error(f"Error in webhook: {e}")
        send_error_to_channel(f"⚠ Ошибка в webhook: {e}")
        return '', 500

# Корневой маршрут
@app.route('/')
def index():
    return 'Bot is running!'

# Graceful shutdown
def shutdown():
    scheduler.shutdown()
    logging.info("Scheduler shutdown gracefully")

atexit.register(shutdown)
signal.signal(signal.SIGTERM, lambda signum, frame: shutdown())

if __name__ == '__main__':
    init_db()
    scheduler.add_job(cleanup_inactive_users, 'interval', weeks=1)
    scheduler.add_job(send_scheduled_task_for_tz, 'interval', minutes=1)
    scheduler.start()
    bot.set_webhook(url=f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/webhook")
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
