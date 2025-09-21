import os
import time  # Для sleep, если потребуется
import telebot
from apscheduler.schedulers.background import BackgroundScheduler
import logging
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from telebot import types
import pendulum
from flask import Flask, request
from tasks import TASKS, ACHIEVEMENTS  # Импорт из tasks.py

# Инициализация
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Конфигурация
TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set.")
bot = telebot.TeleBot(TOKEN)

HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
if not HOSTNAME:
    raise RuntimeError("RENDER_EXTERNAL_HOSTNAME is not set.")
WEBHOOK_URL = f"https://{HOSTNAME}/webhook"

ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set.")
DB_POOL = pool.ThreadedConnectionPool(1, 20, dsn=DATABASE_URL)

# Инициализация БД
def init_db():
    with DB_POOL.getconn() as conn, conn.cursor() as cur:
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
        conn.commit()
        logging.info("Database schema initialized or verified.")
    DB_POOL.putconn(conn)

init_db()

# Работа с БД
def db_execute(query, params=(), fetchone=False, fetchall=False):
    conn = DB_POOL.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            conn.commit()
            if fetchone:
                return cur.fetchone()
            if fetchall:
                return cur.fetchall()
    except Exception as e:
        logging.error(f"DB error: {e}")
        raise
    finally:
        DB_POOL.putconn(conn)

# Логика
def init_user(chat_id, username=None):
    if not db_execute("SELECT 1 FROM users WHERE chat_id = %s", (chat_id,), fetchone=True):
        db_execute("INSERT INTO users (chat_id, username, day) VALUES (%s, %s, 1)", (chat_id, username))

def get_user(chat_id):
    return db_execute("SELECT * FROM users WHERE chat_id = %s", (chat_id,), fetchone=True)

def update_user(chat_id, **kwargs):
    if kwargs:
        allowed_fields = {"day", "streak", "last_done", "achievements", "subscribed", "username", "last_menu_message_id"}
        safe_kwargs = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if safe_kwargs:
            fields = ", ".join(f"{k} = %s" for k in safe_kwargs.keys())
            values = list(safe_kwargs.values()) + [chat_id]
            db_execute(f"UPDATE users SET {fields} WHERE chat_id = %s", tuple(values))

def get_task(user):
    day = user.get('day', 1)
    return TASKS[max(0, min(len(TASKS) - 1, day - 1))]

def check_achievements(user):
    streak = user.get('streak', 0)
    achievements = user.get('achievements', [])
    unlocked = []
    for threshold, text in ACHIEVEMENTS.items():
        if streak >= threshold and str(threshold) not in achievements:
            new_achievements = achievements + [str(threshold)]
            update_user(user['chat_id'], achievements=new_achievements)
            unlocked.append(text)
            achievements = new_achievements
    return unlocked

def next_task(user):
    today = pendulum.now('utc').date()
    streak = (user.get('streak', 0) + 1)
    day = (user.get('day', 1) + 1)
    update_user(user['chat_id'], day=day, streak=streak, last_done=today)
    user = get_user(user['chat_id'])
    return get_task(user), check_achievements(user), user

# UI
def get_inline_keyboard(user):
    subscribed = user.get('subscribed', False)
    return types.InlineKeyboardMarkup(
        row_width=2,
        inline_keyboard=[
            [types.InlineKeyboardButton("📅 Сегодня", callback_data="today"),
             types.InlineKeyboardButton("✅ Выполнено", callback_data="next")],
            [types.InlineKeyboardButton("📊 Статистика", callback_data="stats")],
            [types.InlineKeyboardButton("ℹ Помощь", callback_data="help")],
            [types.InlineKeyboardButton("🔔 Подписаться" if not subscribed else "❌ Отписаться",
                                       callback_data="subscribe" if not subscribed else "unsubscribe")]
        ]
    )

def send_menu(chat_id, user, text):
    try:
        user = get_user(chat_id) or user or {'subscribed': False}
        prev_id = user.get('last_menu_message_id')
        if prev_id and int(prev_id) > 0:
            try:
                bot.edit_message_reply_markup(chat_id, int(prev_id), reply_markup=None)
            except Exception:
                bot.delete_message(chat_id, int(prev_id))
                update_user(chat_id, last_menu_message_id=None)
        msg = bot.send_message(chat_id, text, reply_markup=get_inline_keyboard(user))
        update_user(chat_id, last_menu_message_id=msg.message_id)
    except Exception as e:
        logging.error(f"send_menu error for {chat_id}: {e}")

# Обработчики
@bot.message_handler(commands=['start'])
def start(message):
    init_user(message.chat.id, message.from_user.username)
    send_menu(message.chat.id, get_user(message.chat.id), "Привет 👋 Я твой наставник на 30-дневном пути развития!\n\nНажимай кнопки ниже, чтобы получать задания и отмечать выполнение.")

@bot.message_handler(commands=['stats'])
def stats(message):
    user = get_user(message.chat.id)
    ach_list = [ACHIEVEMENTS.get(int(x), "") for x in user.get('achievements', []) if x.isdigit()]
    ach_text = "🎯 Достижения:\n" + ("\n".join(ach_list) if ach_list else "пока нет")
    send_menu(message.chat.id, user, f"📊 Статистика:\n📅 День: {user.get('day')}/{len(TASKS)}\n🔥 Серия: {user.get('streak', 0)} дней подряд\n{ach_text}")

@bot.message_handler(commands=['all_stats'])
def all_stats(message):
    if str(message.chat.id) != str(ADMIN_ID):
        bot.send_message(message.chat.id, "🚫 Команда доступна только администратору.")
        return
    users = db_execute("SELECT chat_id, username, day, streak FROM users ORDER BY day DESC LIMIT 500;", fetchall=True)
    if not users:
        bot.send_message(message.chat.id, "Нет пользователей.")
        return
    text = "👥 Статистика по пользователям (макс 500):\n" + "\n".join(
        f"- {f'@{u['username']}' if u.get('username') else u['chat_id']}: день {u.get('day')}, серия {u.get('streak')} дней"
        for u in users
    )
    bot.send_message(message.chat.id, text)

@bot.callback_query_handler(func=lambda call: True)
def handle_inline_buttons(call):
    chat_id = call.message.chat.id
    init_user(chat_id, call.from_user.username)
    user = get_user(chat_id)
    data = call.data
    try:
        bot.answer_callback_query(call.id)
    except Exception as e:
        logging.warning(f"Callback error: {e}")
    actions = {
        "today": lambda: send_menu(chat_id, user, f"📌 Сегодня: {get_task(user)}"),
        "next": lambda: (lambda t, a, u: send_menu(chat_id, u, f"➡ Следующее задание:\n{t}\n\n🔥 Серия: {u.get('streak')} дней\n📅 День {u.get('day')}/{len(TASKS)}")(*next_task(user)) or [bot.send_message(chat_id, f"🎉 {a}") for a in user[1]]),
        "stats": lambda: send_menu(chat_id, user, f"📊 Статистика:\n📅 День: {user.get('day')}/{len(TASKS)}\n🔥 Серия: {user.get('streak', 0)} дней подряд\n🎯 Достижения:\n" + ("\n".join([ACHIEVEMENTS.get(int(x), "") for x in user.get('achievements', []) if x.isdigit()]) or "пока нет")),
        "subscribe": lambda: (update_user(chat_id, subscribed=True), send_menu(chat_id, get_user(chat_id), "✅ Напоминания включены! Буду писать в установленное время.")),
        "unsubscribe": lambda: (update_user(chat_id, subscribed=False), send_menu(chat_id, get_user(chat_id), "❌ Ты отписался от напоминаний.")),
        "help": lambda: send_menu(chat_id, user, "ℹ Я помогаю пройти 30-дневную программу совершенствования:\n📅 — показать задание на сегодня\n✅ — отметить выполнение\n📊 — статистика\n🔔 — подписка на напоминания\n\n🎯 Выполняя задания подряд, ты будешь получать достижения!")
    }
    actions.get(data, lambda: None)()

# Планировщик
def send_scheduled_task():
    with DB_POOL.getconn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE subscribed = TRUE;")
            for user in cur.fetchall():
                try:
                    task = get_task(user)
                    bot.send_message(user['chat_id'], f"📌 Автоматическое напоминание:\n{task}\n\nЕсли выполнил(а) — открой бот и нажми ✅.")
                except Exception as e:
                    logging.error(f"Error in scheduled task for {user['chat_id']}: {e}")
    DB_POOL.putconn(conn)

# Роуты
@app.route('/webhook', methods=['POST'])
def webhook():
    if request.method == 'POST':
        update = telebot.types.Update.de_json(request.get_data(as_text=True))
        bot.process_new_updates([update])
        return 'OK', 200
    return 'Not Found', 404

@app.route('/', methods=['GET'])
def health_check():
    return 'OK', 200

# Запуск
if __name__ == '__main__':
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    logging.info(f"🔗 Webhook установлен: {WEBHOOK_URL}")
    scheduler = BackgroundScheduler()
    REMINDER_HOUR = os.getenv("REMINDER_HOUR", "09:00")
    scheduler.add_job(send_scheduled_task, 'cron', hour=int(REMINDER_HOUR.split(':')[0]), minute=int(REMINDER_HOUR.split(':')[1]))
    scheduler.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.shutdown()
        logging.info("Scheduler and application shut down.")