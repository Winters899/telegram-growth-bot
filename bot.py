import os
import time
import telebot
from apscheduler.schedulers.background import BackgroundScheduler
from psycopg2 import pool
from flask import Flask, request
from tasks import TASKS, ACHIEVEMENTS

app = Flask(__name__)
bot = telebot.TeleBot(os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN"))
WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/webhook"
DB_POOL = pool.ThreadedConnectionPool(1, 20, dsn=os.getenv("DATABASE_URL"))

def db_execute(query, params=(), fetchone=False):
    conn = DB_POOL.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            conn.commit()
            return cur.fetchone() if fetchone else None
    finally:
        DB_POOL.putconn(conn)

def init_user(chat_id, username=None):
    if not db_execute("SELECT 1 FROM users WHERE chat_id = %s", (chat_id,), True):
        db_execute("INSERT INTO users (chat_id, username, day) VALUES (%s, %s, 1)", (chat_id, username))

def get_user(chat_id):
    return db_execute("SELECT * FROM users WHERE chat_id = %s", (chat_id,), True)

def update_user(chat_id, **kwargs):
    if kwargs:
        fields = ", ".join(f"{k} = %s" for k in kwargs)
        db_execute(f"UPDATE users SET {fields} WHERE chat_id = %s", list(kwargs.values()) + [chat_id])

def get_task(user):
    day = user.get('day', 1)
    return TASKS[max(0, min(len(TASKS) - 1, day - 1))]

def next_task(user):
    today = pendulum.now('utc').date()
    day, streak = user.get('day', 1) + 1, user.get('streak', 0) + 1
    update_user(user['chat_id'], day=day, streak=streak, last_done=today)
    user = get_user(user['chat_id'])
    return get_task(user), [], user

@bot.message_handler(commands=['start'])
def start(message):
    init_user(message.chat.id, message.from_user.username)
    bot.send_message(message.chat.id, "Привет! Нажимай кнопки для заданий.", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("📅 Сегодня", callback_data="today"), types.InlineKeyboardButton("✅ Выполнено", callback_data="next")]]))

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    init_user(chat_id)
    user = get_user(chat_id)
    data = call.data
    if data == "today":
        bot.edit_message_text(f"📌 Сегодня: {get_task(user)}", chat_id, call.message.message_id)
    elif data == "next":
        task, _, user = next_task(user)
        bot.edit_message_text(f"➡ Задание: {task}\nДень {user['day']}/{len(TASKS)}", chat_id, call.message.message_id)

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.method == 'POST':
        bot.process_new_updates([telebot.types.Update.de_json(request.get_data(as_text=True))])
        return 'OK', 200
    return 'Not Found', 404

@app.route('/', methods=['GET'])
def health_check():
    return 'OK', 200

if __name__ == '__main__':
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: [bot.send_message(u['chat_id'], f"📌 Задание: {get_task(u)}") for u in db_execute("SELECT * FROM users WHERE subscribed = TRUE;", fetchall=True) or []], 'cron', hour=9, minute=0)
    scheduler.start()
    db_execute("CREATE TABLE IF NOT EXISTS users (chat_id BIGINT PRIMARY KEY, day INTEGER DEFAULT 1, streak INTEGER DEFAULT 0, last_done DATE, subscribed BOOLEAN DEFAULT FALSE)")
    while True:
        time.sleep(1)