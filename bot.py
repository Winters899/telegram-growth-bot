import os
import telebot
import schedule
import time
import threading
import logging
import logging.handlers
from flask import Flask, request
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor
from telebot import types
from datetime import datetime, timedelta
from html import escape
from ratelimiter import RateLimiter
import pendulum

# Настройка логирования с ротацией
log_handler = logging.handlers.RotatingFileHandler('bot.log', maxBytes=10*1024*1024, backupCount=5)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[log_handler, logging.StreamHandler()]
)

# Проверка переменных окружения
try:
    TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN или TELEGRAM_TOKEN должны быть установлены.")
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL должен быть установлен.")
    HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if not HOSTNAME:
        raise RuntimeError("RENDER_EXTERNAL_HOSTNAME должен быть установлен.")
    ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID")
    if not ADMIN_ID:
        raise RuntimeError("TELEGRAM_ADMIN_ID должен быть установлен.")
except RuntimeError as e:
    logging.critical(f"Ошибка запуска: {e}")
    exit(1)

# Инициализация бота и вебхука
bot = telebot.TeleBot(TOKEN)
WEBHOOK_URL = f"https://{HOSTNAME}/webhook"
app = Flask(__name__)

# Пул подключений к базе данных
DATABASE_POOL = SimpleConnectionPool(1, 20, dsn=DATABASE_URL)

# Ограничитель скорости для Telegram API
rate_limiter = RateLimiter(max_calls=30, period=1)

# Блокировка для потокобезопасного доступа к базе данных
DB_LOCK = threading.Lock()

# Настройка часового пояса
TIMEZONE = os.getenv("BOT_TIMEZONE", "UTC")
REMINDER_HOUR = os.getenv("REMINDER_HOUR", "09:00")

# Список заданий (для начальной загрузки в БД)
TASKS = [
    "День 1: Определи 10 ключевых целей на ближайший год.",
    "День 2: Составь утренний ритуал (вода, зарядка, визуализация).",
    "День 3: Откажись от одной вредной привычки.",
    "День 4: Веди дневник мыслей и благодарностей.",
    "День 5: Составь список из 10 сильных сторон.",
    "День 6: Сделай цифровой детокс на 6 часов.",
    "День 7: Подведи итоги недели, отметь победы.",
    "День 8: Применяй правило Парето 20/80.",
    "День 9: Определи 3 главные приоритеты дня.",
    "День 10: Используй технику Pomodoro (25/5).",
    "День 11: Наведи порядок на рабочем месте.",
    "День 12: Минимизируй отвлекающие факторы.",
    "День 13: Сделай 2 часа глубокой работы.",
    "День 14: Итоги недели: оцени продуктивность.",
    "День 15: Напиши свою миссию и ценности.",
    "День 16: Практикуй публичные мини-выступления.",
    "День 17: Научись говорить «нет».",
    "День 18: Прочитай биографию лидера.",
    "День 19: Сделай доброе дело.",
    "День 20: Визуализируй себя через 5 лет.",
    "День 21: Итоги недели: оцени уверенность.",
    "День 22: Составь план учёбы на 1 год.",
    "День 23: Определи наставника.",
    "День 24: Практикуй вечерний анализ.",
    "День 25: Составь финансовую стратегию.",
    "День 26: Сделай ревизию окружения.",
    "День 27: Поделись знаниями.",
    "День 28: Итоги: составь план на месяц.",
    "День 29: Определи 3 долгосрочные мечты.",
    "День 30: Создай карту жизни."
]

# Достижения
ACHIEVEMENTS = {
    5: "🏅 Молодец! 5 дней подряд!",
    10: "🥈 Ты машина! 10 дней без перерыва!",
    20: "🥇 Железная сила воли! 20 дней подряд!",
    30: "👑 Герой челленджа! 30 дней!"
}

# Управление подключениями к базе данных
def get_db():
    return DATABASE_POOL.getconn()

def release_db(conn):
    DATABASE_POOL.putconn(conn)

# Инициализация базы данных
def init_db():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Создание таблицы users
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id BIGINT PRIMARY KEY,
                day INTEGER DEFAULT 1,
                streak INTEGER DEFAULT 0,
                last_done DATE,
                achievements TEXT[] DEFAULT '{}',
                subscribed BOOLEAN DEFAULT FALSE,
                username TEXT,
                last_menu_message_id INTEGER
            );
            """)
            # Создание таблицы tasks
            cur.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                day INTEGER PRIMARY KEY,
                description TEXT NOT NULL
            );
            """)
            # Загрузка заданий в таблицу tasks, если она пуста
            cur.execute("SELECT COUNT(*) FROM tasks")
            if cur.fetchone()['count'] == 0:
                for i, task in enumerate(TASKS, 1):
                    cur.execute("INSERT INTO tasks (day, description) VALUES (%s, %s)", (i, task))
            conn.commit()
        release_db(conn)
    logging.info("Схема базы данных инициализирована.")

init_db()

# Работа с пользователями
def init_user(chat_id, username=None):
    safe_username = escape(username) if username else None
    with DB_LOCK:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE chat_id = %s", (chat_id,))
                user = cur.fetchone()
                if not user:
                    cur.execute("INSERT INTO users (chat_id, username, day) VALUES (%s, %s, %s)", (chat_id, safe_username, 1))
                    conn.commit()
            release_db(conn)

def get_user(chat_id):
    with DB_LOCK:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE chat_id = %s", (chat_id,))
                user = cur.fetchone()
            release_db(conn)
        return user

def update_user(chat_id, **kwargs):
    if not kwargs:
        return
    allowed_fields = {
        "day", "streak", "last_done", "achievements",
        "subscribed", "username", "last_menu_message_id"
    }
    safe_kwargs = {k: v for k, v in kwargs.items() if k in allowed_fields}
    if not safe_kwargs:
        logging.warning(f"update_user: нет допустимых полей для обновления {chat_id}: {list(kwargs.keys())}")
        return

    with DB_LOCK:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                try:
                    fields = ", ".join([f"{k} = %s" for k in safe_kwargs.keys()])
                    values = list(safe_kwargs.values())
                    values.append(chat_id)
                    cur.execute(f"UPDATE users SET {fields} WHERE chat_id = %s", tuple(values))
                    conn.commit()
                except Exception as e:
                    logging.warning(f"Ошибка update_user для {chat_id}: {e}")
            release_db(conn)

# Получение задания из базы данных
def get_task(user):
    with DB_LOCK:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                day = user.get('day') or 1
                cur.execute("SELECT description FROM tasks WHERE day = %s", (day,))
                task = cur.fetchone()
                return task['description'] if task else "Задание не найдено"
            release_db(conn)

# Проверка достижений
def check_achievements(user):
    unlocked = []
    current_streak = user.get('streak') or 0
    existing = user.get('achievements') or []
    for threshold, text in ACHIEVEMENTS.items():
        str_threshold = str(threshold)
        if current_streak >= threshold and str_threshold not in existing:
            new_achievements = existing + [str_threshold]
            update_user(user['chat_id'], achievements=new_achievements)
            unlocked.append(text)
            existing = new_achievements
    return unlocked

# Переход к следующему заданию
def next_task(user):
    today = datetime.utcnow().date()
    last_done = user.get('last_done')
    streak = user.get('streak') or 0

    if last_done:
        if today == last_done + timedelta(days=1):
            streak += 1
        elif today == last_done:
            pass
        else:
            streak = 1
    else:
        streak = 1

    current_day = user.get('day') or 1
    with DB_LOCK:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT COUNT(*) FROM tasks")
                max_days = cur.fetchone()['count']
            release_db(conn)
    new_day = current_day + 1 if current_day < max_days else current_day
    update_user(user['chat_id'], day=new_day, streak=streak, last_done=today)
    user = get_user(user['chat_id'])
    return get_task(user), check_achievements(user), user

# Очистка неактивных пользователей
def cleanup_inactive_users():
    with DB_LOCK:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                threshold = datetime.utcnow().date() - timedelta(days=90)
                cur.execute("DELETE FROM users WHERE last_done < %s", (threshold,))
                conn.commit()
                logging.info(f"Удалено {cur.rowcount} неактивных пользователей")
            release_db(conn)

# Инлайн-клавиатура
def get_inline_keyboard(user):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(
        types.InlineKeyboardButton("📅 Сегодня", callback_data="today"),
        types.InlineKeyboardButton("✅ Выполнено", callback_data="next")
    )
    keyboard.add(types.InlineKeyboardButton("📊 Статистика", callback_data="stats"))
    keyboard.add(types.InlineKeyboardButton("ℹ Помощь", callback_data="help"))
    keyboard.add(
        types.InlineKeyboardButton(
            "🔔 Подписаться" if not user.get('subscribed', False) else "❌ Отписаться",
            callback_data="subscribe" if not user.get('subscribed', False) else "unsubscribe"
        )
    )
    return keyboard

# Отправка сообщений с ограничением скорости и повторными попытками
def send_message_with_rate_limit(chat_id, text, **kwargs):
    with rate_limiter:
        for attempt in range(3):
            try:
                return bot.send_message(chat_id, text, **kwargs)
            except Exception as e:
                logging.warning(f"Повтор {attempt+1}/3: Ошибка отправки сообщения {chat_id}: {e}")
                time.sleep(2 ** attempt)
        logging.error(f"Не удалось отправить сообщение {chat_id} после попыток")
        return None

# Отправка меню
def send_menu(chat_id, user, text):
    try:
        fresh_user = get_user(chat_id) or user or {'subscribed': False}
        prev_id = fresh_user.get('last_menu_message_id')
        if prev_id:
            try:
                bot.delete_message(chat_id, prev_id)
                logging.debug(f"Удалено предыдущее меню {prev_id} для {chat_id}")
            except Exception:
                logging.debug(f"Нет предыдущего меню для удаления в {chat_id}")
            update_user(chat_id, last_menu_message_id=None)

        msg = send_message_with_rate_limit(chat_id, text, reply_markup=get_inline_keyboard(fresh_user))
        if msg:
            update_user(chat_id, last_menu_message_id=msg.message_id)
        else:
            raise Exception("Не удалось отправить сообщение")
    except Exception as e:
        logging.error(f"Ошибка send_menu для {chat_id}: {e}")
        send_message_with_rate_limit(chat_id, "⚠ Что-то пошло не так. Попробуй позже!")

# Команда /start
@bot.message_handler(commands=['start'])
def start(message):
    init_user(message.chat.id, message.from_user.username)
    user = get_user(message.chat.id)
    send_menu(
        message.chat.id,
        user,
        "Привет 👋 Я твой наставник на 30-дневном пути развития!\n\n"
        "Нажимай кнопки ниже, чтобы получать задания и отмечать выполнение."
    )

# Команда /stats
@bot.message_handler(commands=['stats'])
def stats(message):
    user = get_user(message.chat.id)
    ach_list = []
    for x in (user.get('achievements') or []):
        try:
            ach_list.append(ACHIEVEMENTS.get(int(x), ""))
        except Exception:
            pass
    ach_text = "🎯 Достижения:\n" + ("\n".join(ach_list) if ach_list else "пока нет")
    with DB_LOCK:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT COUNT(*) FROM tasks")
                total_days = cur.fetchone()['count']
            release_db(conn)
    send_menu(
        message.chat.id,
        user,
        f"📊 Статистика:\n📅 День: {user.get('day')}/{total_days}\n🔥 Серия: {user.get('streak') or 0} дней подряд\n{ach_text}"
    )

# Команда /all_stats (только для админа)
@bot.message_handler(commands=['all_stats'])
def all_stats(message):
    if str(message.chat.id) != str(ADMIN_ID):
        logging.warning(f"Несанкционированная попытка /all_stats от {message.chat.id}")
        send_message_with_rate_limit(message.chat.id, "🚫 Команда доступна только администратору.")
        return

    with DB_LOCK:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT chat_id, username, day, streak FROM users ORDER BY day DESC LIMIT 500;")
                users = cur.fetchall()
            release_db(conn)

    if not users:
        send_message_with_rate_limit(message.chat.id, "Нет пользователей.")
        return

    text = "👥 Статистика по пользователям (макс 500):\n"
    for u in users:
        uname = f"@{u['username']}" if u.get('username') else u['chat_id']
        text += f"- {uname}: день {u.get('day')}, серия {u.get('streak')} дней\n"
    send_message_with_rate_limit(message.chat.id, text)

# Обработка инлайн-кнопок
@bot.callback_query_handler(func=lambda call: True)
def handle_inline_buttons(call):
    chat_id = call.message.chat.id
    init_user(chat_id, call.from_user.username)
    user = get_user(chat_id)
    data = call.data

    try:
        bot.answer_callback_query(call.id)
    except Exception as e:
        logging.warning(f"Ошибка callback: {e}")

    if data == "today":
        send_menu(chat_id, user, f"📌 Сегодня: {get_task(user)}")

    elif data == "next":
        task, achievements, user = next_task(user)
        with DB_LOCK:
            with get_db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT COUNT(*) FROM tasks")
                    total_days = cur.fetchone()['count']
                release_db(conn)
        text = f"➡ Следующее задание:\n{task}\n\n🔥 Серия: {user.get('streak')} дней\n📅 День {user.get('day')}/{total_days}"
        send_menu(chat_id, user, text)
        for ach in achievements:
            send_message_with_rate_limit(chat_id, f"🎉 {ach}")

    elif data == "stats":
        ach_list = []
        for x in (user.get('achievements') or []):
            try:
                ach_list.append(ACHIEVEMENTS.get(int(x), ""))
            except Exception:
                pass
        ach_text = "🎯 Достижения:\n" + ("\n".join(ach_list) if ach_list else "пока нет")
        with DB_LOCK:
            with get_db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT COUNT(*) FROM tasks")
                    total_days = cur.fetchone()['count']
                release_db(conn)
        send_menu(
            chat_id,
            user,
            f"📊 Статистика:\n📅 День: {user.get('day')}/{total_days}\n🔥 Серия: {user.get('streak') or 0} дней подряд\n{ach_text}"
        )

    elif data == "subscribe":
        update_user(chat_id, subscribed=True)
        user = get_user(chat_id)
        send_menu(chat_id, user, "✅ Напоминания включены! Буду писать в установленное время.")

    elif data == "unsubscribe":
        update_user(chat_id, subscribed=False)
        user = get_user(chat_id)
        send_menu(chat_id, user, "❌ Ты отписался от напоминаний.")

    elif data == "help":
        send_menu(
            chat_id,
            user,
            "ℹ Я помогаю пройти 30-дневную программу совершенствования:\n"
            "📅 — показать задание на сегодня\n"
            "✅ — отметить выполнение\n"
            "📊 — статистика\n"
            "🔔 — подписка на напоминания\n\n"
            "🎯 Выполняя задания подряд, ты будешь получать достижения!"
        )

# Планировщик напоминаний
def send_scheduled_task():
    now = pendulum.now(TIMEZONE)
    with DB_LOCK:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE subscribed = TRUE;")
                subs = cur.fetchall()
            release_db(conn)

    for user in subs:
        try:
            task = get_task(user)
            send_message_with_rate_limit(
                user['chat_id'],
                f"📌 Напоминание ({now.to_formatted_date_string()}):\n{task}\n\nЕсли выполнил(а) — открой бот и нажми ✅."
            )
        except Exception as e:
            logging.error(f"Ошибка в запланированном задании для {user['chat_id']}: {e}")

def schedule_checker():
    while True:
        schedule.run_pending()
        time.sleep(30)

# Вебхук-сервер
@app.route('/webhook', methods=['POST'])
def webhook():
    body = request.get_data(as_text=True)
    try:
        update = telebot.types.Update.de_json(body)
        if update.message:
            user = update.message.from_user
            logging.info(f"📩 Сообщение от @{user.username or user.id}: {getattr(update.message, 'text', '')}")
        elif update.callback_query:
            user = update.callback_query.from_user
            logging.info(f"🔘 Callback от @{user.username or user.id}: {update.callback_query.data}")
        bot.process_new_updates([update])
        return '', 200
    except Exception as e:
        logging.error(f"Ошибка обработки вебхука: {e}")
        return '', 400

@app.route('/')
def index():
    return "Привет, я жив!", 200

# Запуск
if __name__ == '__main__':
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    logging.info(f"🔗 Вебхук установлен: {WEBHOOK_URL}")

    schedule.every().day.at(REMINDER_HOUR).do(send_scheduled_task)
    schedule.every().week.do(cleanup_inactive_users)
    threading.Thread(target=schedule_checker, daemon=True).start()

    port = int(os.getenv("PORT", 10000))
    app.run(host='0.0.0.0', port=port)