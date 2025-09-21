import os
import telebot
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from telebot import types
from datetime import date
from flask import Flask, request
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
import atexit

# Логирование
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Инициализация Flask
app = Flask(__name__)

# Токен и настройки
TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set.")
bot = telebot.TeleBot(TOKEN)

HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
if not HOSTNAME:
    raise RuntimeError("RENDER_EXTERNAL_HOSTNAME is not set.")
WEBHOOK_URL = f"https://{HOSTNAME}/webhook"

ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID")
if not ADMIN_ID:
    raise RuntimeError("TELEGRAM_ADMIN_ID is not set.")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set.")

# Пул соединений с БД
db_pool = ThreadedConnectionPool(1, 20, DATABASE_URL, sslmode='require')

# Задания
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

# Инициализация БД
def init_db():
    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
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
                last_action TIMESTAMP
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_subscribed ON users (subscribed);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_chat_id ON users (chat_id);")
        conn.commit()
        logging.info("Database schema initialized.")
    except psycopg2.Error as e:
        logging.error(f"Database initialization failed: {e}")
        raise
    finally:
        cur.close()
        db_pool.putconn(conn)

init_db()

# Работа с БД
def get_db():
    return db_pool.getconn()

def release_db(conn):
    db_pool.putconn(conn)

def init_user(chat_id, username=None):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE chat_id = %s", (chat_id,))
        user = cur.fetchone()
        if not user:
            cur.execute("INSERT INTO users (chat_id, username, day) VALUES (%s, %s, %s)", (chat_id, username, 1))
            conn.commit()
    except Exception as e:
        logging.error(f"init_user error for {chat_id}: {e}")
    finally:
        cur.close()
        release_db(conn)

def get_user(chat_id):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE chat_id = %s", (chat_id,))
        return cur.fetchone()
    except Exception as e:
        logging.error(f"get_user error for {chat_id}: {e}")
        return None
    finally:
        cur.close()
        release_db(conn)

def update_user(chat_id, **kwargs):
    if not kwargs:
        return
    allowed_fields = {"day", "streak", "last_done", "achievements", "subscribed", "username", "last_menu_message_id", "last_action"}
    safe_kwargs = {k: v for k, v in kwargs.items() if k in allowed_fields}
    if not safe_kwargs:
        logging.warning(f"update_user: no allowed fields for {chat_id}: {list(kwargs.keys())}")
        return
    conn = get_db()
    try:
        cur = conn.cursor()
        fields = ", ".join([f"{k} = %s" for k in safe_kwargs.keys()])
        values = list(safe_kwargs.values())
        values.append(chat_id)
        cur.execute(f"UPDATE users SET {fields} WHERE chat_id = %s", tuple(values))
        conn.commit()
    except Exception as e:
        logging.error(f"update_user error for {chat_id}: {e}")
    finally:
        cur.close()
        release_db(conn)

# Логика заданий
def get_task(user):
    day = user.get('day', 1)
    idx = max(0, min(len(TASKS)-1, day-1))
    return TASKS[idx]

def check_achievements(user):
    unlocked = []
    current_streak = user.get('streak', 0)
    existing = user.get('achievements', [])
    for threshold, text in ACHIEVEMENTS.items():
        str_threshold = str(threshold)
        if current_streak >= threshold and str_threshold not in existing:
            unlocked.append(text)
            existing.append(str_threshold)
            update_user(user['chat_id'], achievements=existing)
    return unlocked

def next_task(user):
    from datetime import datetime
    today = date.today()
    streak = user.get('streak', 0)
    last_done = user.get('last_done')
    current_day = user.get('day', 1)

    if last_done == today:
        return get_task(user), [], user  # Уже выполнено сегодня

    if last_done and (today - last_done).days > 1:
        streak = 0  # Сброс серии при пропуске

    streak += 1
    new_day = current_day + 1 if current_day < len(TASKS) else 1  # Зациклить после 30
    update_user(user['chat_id'], day=new_day, streak=streak, last_done=today, last_action=datetime.now())
    user = get_user(user['chat_id'])
    return get_task(user), check_achievements(user), user

# Кнопки
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

def send_menu(chat_id, user, text):
    try:
        fresh_user = get_user(chat_id) or user or {'subscribed': False}
        prev_id = fresh_user.get('last_menu_message_id')
        if prev_id:
            try:
                bot.edit_message_reply_markup(chat_id=chat_id, message_id=prev_id, reply_markup=None)
            except Exception as e:
                logging.debug(f"edit_message_reply_markup failed for {prev_id}: {e}")
                try:
                    bot.delete_message(chat_id, prev_id)
                except Exception as e_del:
                    logging.debug(f"delete_message failed for {prev_id}: {e_del}")

        msg = bot.send_message(chat_id, text, reply_markup=get_inline_keyboard(fresh_user))
        update_user(chat_id, last_menu_message_id=msg.message_id)
    except Exception as e:
        logging.error(f"send_menu error for {chat_id}: {e}")

# Обработчики
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

@bot.message_handler(commands=['stats'])
def stats(message):
    user = get_user(message.chat.id)
    if not user:
        return
    ach_list = [ACHIEVEMENTS.get(int(x), "") for x in (user.get('achievements') or []) if x.isdigit()]
    ach_text = "🎯 Достижения:\n" + ("\n".join(ach_list) if ach_list else "пока нет")
    send_menu(
        message.chat.id,
        user,
        f"📊 Статистика:\n📅 День: {user.get('day')}/{len(TASKS)}\n🔥 Серия: {user.get('streak', 0)} дней подряд\n{ach_text}"
    )

@bot.message_handler(commands=['all_stats'])
def all_stats(message):
    if str(message.chat.id) != str(ADMIN_ID):
        bot.send_message(message.chat.id, "🚫 Команда доступна только администратору.")
        return
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT chat_id, username, day, streak FROM users ORDER BY day DESC LIMIT 500")
        users = cur.fetchall()
        text = "👥 Статистика по пользователям (макс 500):\n"
        for u in users:
            uname = f"@{u['username']}" if u.get('username') else u['chat_id']
            text += f"- {uname}: день {u.get('day')}, серия {u.get('streak')} дней\n"
        bot.send_message(message.chat.id, text or "Нет пользователей.")
    except Exception as e:
        logging.error(f"all_stats error: {e}")
    finally:
        cur.close()
        release_db(conn)

@bot.callback_query_handler(func=lambda call: True)
def handle_inline_buttons(call):
    from datetime import datetime, timedelta
    chat_id = call.message.chat.id
    init_user(chat_id, call.from_user.username)
    user = get_user(chat_id)
    if not user:
        return

    # Простое ограничение частоты (1 действие в 2 секунды)
    last_action = user.get('last_action')
    if last_action and (datetime.now() - last_action).total_seconds() < 2:
        bot.answer_callback_query(call.id, "Слишком быстро! Подождите пару секунд.")
        return

    try:
        bot.answer_callback_query(call.id)
    except Exception as e:
        logging.error(f"Callback error for {chat_id}: {e}")

    update_user(chat_id, last_action=datetime.now())
    data = call.data

    if data == "today":
        send_menu(chat_id, user, f"📌 Сегодня: {get_task(user)}")

    elif data == "next":
        task, achievements, user = next_task(user)
        text = f"➡ Следующее задание:\n{task}\n\n🔥 Серия: {user.get('streak')} дней\n📅 День {user.get('day')}/{len(TASKS)}"
        send_menu(chat_id, user, text)
        for ach in achievements:
            try:
                bot.send_message(chat_id, f"🎉 {ach}")
            except Exception as e:
                logging.error(f"Failed to send achievement to {chat_id}: {e}")

    elif data == "stats":
        ach_list = [ACHIEVEMENTS.get(int(x), "") for x in (user.get('achievements') or []) if x.isdigit()]
        ach_text = "🎯 Достижения:\n" + ("\n".join(ach_list) if ach_list else "пока нет")
        send_menu(
            chat_id,
            user,
            f"📊 Статистика:\n📅 День: {user.get('day')}/{len(TASKS)}\n🔥 Серия: {user.get('streak', 0)} дней подряд\n{ach_text}"
        )

    elif data == "subscribe":
        update_user(chat_id, subscribed=True)
        user = get_user(chat_id)
        send_menu(chat_id, user, "✅ Напоминания включены! Буду писать в 09:00 UTC.")

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

# Напоминания
def send_scheduled_task():
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE subscribed = TRUE")
        subs = cur.fetchall()
        for user in subs:
            try:
                task = get_task(user)
                bot.send_message(user['chat_id'], f"📌 Напоминание:\n{task}\n\nЕсли выполнил(а) — открой бот и нажми ✅.")
            except Exception as e:
                logging.error(f"Scheduled task error for {user['chat_id']}: {e}")
    except Exception as e:
        logging.error(f"send_scheduled_task error: {e}")
    finally:
        cur.close()
        release_db(conn)

# Вебхук
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = telebot.types.Update.de_json(request.get_json(force=True))
        bot.process_new_updates([update])
        return 'OK', 200
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return 'Bad Request', 400

# Запуск
if __name__ == '__main__':
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    logging.info(f"Webhook set: {WEBHOOK_URL}")

    jobstores = {'default': SQLAlchemyJobStore(url=DATABASE_URL.replace('postgres://', 'postgresql://'))}
    scheduler = BackgroundScheduler(jobstores=jobstores)
    scheduler.add_job(send_scheduled_task, 'cron', hour=9, minute=0)
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())

    # Gunicorn будет запускать приложение, app.run() только для локального тестирования
    # app.run(host='0.0.0.0', port=int(os.getenv("PORT", 10000)))
