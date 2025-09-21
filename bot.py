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
from datetime import datetime, timedelta, timezone
from html import escape
import pendulum
import random
from collections import deque
from time import monotonic
from statistics import mean
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# РљР°СЃС‚РѕРјРЅС‹Р№ РѕРіСЂР°РЅРёС‡РёС‚РµР»СЊ СЃРєРѕСЂРѕСЃС‚Рё
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
                time.sleep(self.period - (monotonic() - self.calls[0]))
        self.calls.append(monotonic())
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

# РќР°СЃС‚СЂРѕР№РєР° Р»РѕРіРёСЂРѕРІР°РЅРёСЏ СЃ СЂРѕС‚Р°С†РёРµР№
log_handler = logging.handlers.RotatingFileHandler('bot.log', maxBytes=10*1024*1024, backupCount=5)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[log_handler, logging.StreamHandler()]
)

# РџСЂРѕРІРµСЂРєР° РїРµСЂРµРјРµРЅРЅС‹С… РѕРєСЂСѓР¶РµРЅРёСЏ
try:
    TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN РёР»Рё TELEGRAM_TOKEN РґРѕР»Р¶РЅС‹ Р±С‹С‚СЊ СѓСЃС‚Р°РЅРѕРІР»РµРЅС‹.")
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ СѓСЃС‚Р°РЅРѕРІР»РµРЅ.")
    HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if not HOSTNAME:
        raise RuntimeError("RENDER_EXTERNAL_HOSTNAME РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ СѓСЃС‚Р°РЅРѕРІР»РµРЅ.")
    ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID")
    if not ADMIN_ID:
        raise RuntimeError("TELEGRAM_ADMIN_ID РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ СѓСЃС‚Р°РЅРѕРІР»РµРЅ.")
except RuntimeError as e:
    logging.critical(f"РћС€РёР±РєР° Р·Р°РїСѓСЃРєР°: {e}")
    exit(1)

# РРЅРёС†РёР°Р»РёР·Р°С†РёСЏ Р±РѕС‚Р° Рё РІРµР±С…СѓРєР°
bot = telebot.TeleBot(TOKEN)
WEBHOOK_URL = f"https://{HOSTNAME}/webhook"
app = Flask(__name__)

# РџСѓР» РїРѕРґРєР»СЋС‡РµРЅРёР№ Рє Р±Р°Р·Рµ РґР°РЅРЅС‹С…
DATABASE_POOL = SimpleConnectionPool(1, 20, dsn=DATABASE_URL)

# РћРіСЂР°РЅРёС‡РёС‚РµР»СЊ СЃРєРѕСЂРѕСЃС‚Рё РґР»СЏ Telegram API
rate_limiter = RateLimiter(max_calls=30, period=1)

# Р‘Р»РѕРєРёСЂРѕРІРєР° РґР»СЏ РїРѕС‚РѕРєРѕР±РµР·РѕРїР°СЃРЅРѕРіРѕ РґРѕСЃС‚СѓРїР° Рє Р±Р°Р·Рµ РґР°РЅРЅС‹С…
DB_LOCK = threading.Lock()

# РќР°СЃС‚СЂРѕР№РєР° РІСЂРµРјРµРЅРё РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ
DEFAULT_TIMEZONE = os.getenv("BOT_TIMEZONE", "UTC")
REMINDER_HOUR = os.getenv("REMINDER_HOUR", "09:00")

# РљСЌС€ РґР»СЏ РїСЂРµРґРѕС‚РІСЂР°С‰РµРЅРёСЏ СЃРїР°РјР° РєРЅРѕРїРєР°РјРё
last_callback_time = {}

# РљСЌС€ РґР»СЏ РѕС‚СЃР»РµР¶РёРІР°РЅРёСЏ Р·Р°РґРµСЂР¶РµРє callback
callback_delays = deque(maxlen=100)

# РњРѕС‚РёРІР°С†РёРѕРЅРЅС‹Рµ С†РёС‚Р°С‚С‹
MOTIVATIONAL_QUOTES = [
    "РљР°Р¶РґС‹Р№ С€Р°Рі РїСЂРёР±Р»РёР¶Р°РµС‚ С‚РµР±СЏ Рє С†РµР»Рё! рџљЂ",
    "РўС‹ РґРµР»Р°РµС€СЊ СЌС‚Рѕ! РџСЂРѕРґРѕР»Р¶Р°Р№ СЃРёСЏС‚СЊ! рџЊџ",
    "РњР°Р»РµРЅСЊРєРёРµ РґРµР№СЃС‚РІРёСЏ РїСЂРёРІРѕРґСЏС‚ Рє Р±РѕР»СЊС€РёРј СЂРµР·СѓР»СЊС‚Р°С‚Р°Рј! рџ’Є",
    "РўРІРѕСЏ РґРёСЃС†РёРїР»РёРЅР° вЂ” С‚РІРѕСЏ СЃСѓРїРµСЂСЃРёР»Р°! рџ¦ё"
]

# РЎРїРёСЃРѕРє РїРѕРїСѓР»СЏСЂРЅС‹С… С‡Р°СЃРѕРІС‹С… РїРѕСЏСЃРѕРІ РґР»СЏ РІС‹Р±РѕСЂР°
TIMEZONES = [
    "Europe/Moscow",
    "Europe/London",
    "America/New_York",
    "America/Los_Angeles",
    "Asia/Tokyo",
    "Australia/Sydney",
    "UTC"
]

# РЎРїРёСЃРѕРє Р·Р°РґР°РЅРёР№
TASKS = [
    "Р”РµРЅСЊ 1: РћРїСЂРµРґРµР»Рё 10 РєР»СЋС‡РµРІС‹С… С†РµР»РµР№ РЅР° Р±Р»РёР¶Р°Р№С€РёР№ РіРѕРґ.",
    "Р”РµРЅСЊ 2: РЎРѕСЃС‚Р°РІСЊ СѓС‚СЂРµРЅРЅРёР№ СЂРёС‚СѓР°Р» (РІРѕРґР°, Р·Р°СЂСЏРґРєР°, РІРёР·СѓР°Р»РёР·Р°С†РёСЏ).",
    "Р”РµРЅСЊ 3: РћС‚РєР°Р¶РёСЃСЊ РѕС‚ РѕРґРЅРѕР№ РІСЂРµРґРЅРѕР№ РїСЂРёРІС‹С‡РєРё.",
    "Р”РµРЅСЊ 4: Р’РµРґРё РґРЅРµРІРЅРёРє РјС‹СЃР»РµР№ Рё Р±Р»Р°РіРѕРґР°СЂРЅРѕСЃС‚РµР№.",
    "Р”РµРЅСЊ 5: РЎРѕСЃС‚Р°РІСЊ СЃРїРёСЃРѕРє РёР· 10 СЃРёР»СЊРЅС‹С… СЃС‚РѕСЂРѕРЅ.",
    "Р”РµРЅСЊ 6: РЎРґРµР»Р°Р№ С†РёС„СЂРѕРІРѕР№ РґРµС‚РѕРєСЃ РЅР° 6 С‡Р°СЃРѕРІ.",
    "Р”РµРЅСЊ 7: РџРѕРґРІРµРґРё РёС‚РѕРіРё РЅРµРґРµР»Рё, РѕС‚РјРµС‚СЊ РїРѕР±РµРґС‹.",
    "Р”РµРЅСЊ 8: РџСЂРёРјРµРЅСЏР№ РїСЂР°РІРёР»Рѕ РџР°СЂРµС‚Рѕ 20/80.",
    "Р”РµРЅСЊ 9: РћРїСЂРµРґРµР»Рё 3 РіР»Р°РІРЅС‹Рµ РїСЂРёРѕСЂРёС‚РµС‚С‹ РґРЅСЏ.",
    "Р”РµРЅСЊ 10: РСЃРїРѕР»СЊР·СѓР№ С‚РµС…РЅРёРєСѓ Pomodoro (25/5).",
    "Р”РµРЅСЊ 11: РќР°РІРµРґРё РїРѕСЂСЏРґРѕРє РЅР° СЂР°Р±РѕС‡РµРј РјРµСЃС‚Рµ.",
    "Р”РµРЅСЊ 12: РњРёРЅРёРјРёР·РёСЂСѓР№ РѕС‚РІР»РµРєР°СЋС‰РёРµ С„Р°РєС‚РѕСЂС‹.",
    "Р”РµРЅСЊ 13: РЎРґРµР»Р°Р№ 2 С‡Р°СЃР° РіР»СѓР±РѕРєРѕР№ СЂР°Р±РѕС‚С‹.",
    "Р”РµРЅСЊ 14: РС‚РѕРіРё РЅРµРґРµР»Рё: РѕС†РµРЅРё РїСЂРѕРґСѓРєС‚РёРІРЅРѕСЃС‚СЊ.",
    "Р”РµРЅСЊ 15: РќР°РїРёС€Рё СЃРІРѕСЋ РјРёСЃСЃРёСЋ Рё С†РµРЅРЅРѕСЃС‚Рё.",
    "Р”РµРЅСЊ 16: РџСЂР°РєС‚РёРєСѓР№ РїСѓР±Р»РёС‡РЅС‹Рµ РјРёРЅРё-РІС‹СЃС‚СѓРїР»РµРЅРёСЏ.",
    "Р”РµРЅСЊ 17: РќР°СѓС‡РёСЃСЊ РіРѕРІРѕСЂРёС‚СЊ В«РЅРµС‚В».",
    "Р”РµРЅСЊ 18: РџСЂРѕС‡РёС‚Р°Р№ Р±РёРѕРіСЂР°С„РёСЋ Р»РёРґРµСЂР°.",
    "Р”РµРЅСЊ 19: РЎРґРµР»Р°Р№ РґРѕР±СЂРѕРµ РґРµР»Рѕ.",
    "Р”РµРЅСЊ 20: Р’РёР·СѓР°Р»РёР·РёСЂСѓР№ СЃРµР±СЏ С‡РµСЂРµР· 5 Р»РµС‚.",
    "Р”РµРЅСЊ 21: РС‚РѕРіРё РЅРµРґРµР»Рё: РѕС†РµРЅРё СѓРІРµСЂРµРЅРЅРѕСЃС‚СЊ.",
    "Р”РµРЅСЊ 22: РЎРѕСЃС‚Р°РІСЊ РїР»Р°РЅ СѓС‡С‘Р±С‹ РЅР° 1 РіРѕРґ.",
    "Р”РµРЅСЊ 23: РћРїСЂРµРґРµР»Рё РЅР°СЃС‚Р°РІРЅРёРєР°.",
    "Р”РµРЅСЊ 24: РџСЂР°РєС‚РёРєСѓР№ РІРµС‡РµСЂРЅРёР№ Р°РЅР°Р»РёР·.",
    "Р”РµРЅСЊ 25: РЎРѕСЃС‚Р°РІСЊ С„РёРЅР°РЅСЃРѕРІСѓСЋ СЃС‚СЂР°С‚РµРіРёСЋ.",
    "Р”РµРЅСЊ 26: РЎРґРµР»Р°Р№ СЂРµРІРёР·РёСЋ РѕРєСЂСѓР¶РµРЅРёСЏ.",
    "Р”РµРЅСЊ 27: РџРѕРґРµР»РёСЃСЊ Р·РЅР°РЅРёСЏРјРё.",
    "Р”РµРЅСЊ 28: РС‚РѕРіРё: СЃРѕСЃС‚Р°РІСЊ РїР»Р°РЅ РЅР° РјРµСЃСЏС†.",
    "Р”РµРЅСЊ 29: РћРїСЂРµРґРµР»Рё 3 РґРѕР»РіРѕСЃСЂРѕС‡РЅС‹Рµ РјРµС‡С‚С‹.",
    "Р”РµРЅСЊ 30: РЎРѕР·РґР°Р№ РєР°СЂС‚Сѓ Р¶РёР·РЅРё."
]

# Р”РѕСЃС‚РёР¶РµРЅРёСЏ
ACHIEVEMENTS = {
    5: "рџЏ… РњРѕР»РѕРґРµС†! 5 РґРЅРµР№ РїРѕРґСЂСЏРґ!",
    10: "рџҐ€ РўС‹ РјР°С€РёРЅР°! 10 РґРЅРµР№ Р±РµР· РїРµСЂРµСЂС‹РІР°!",
    20: "рџҐ‡ Р–РµР»РµР·РЅР°СЏ СЃРёР»Р° РІРѕР»Рё! 20 РґРЅРµР№ РїРѕРґСЂСЏРґ!",
    30: "рџ‘‘ Р“РµСЂРѕР№ С‡РµР»Р»РµРЅРґР¶Р°! 30 РґРЅРµР№!"
}

# РЈРїСЂР°РІР»РµРЅРёРµ РїРѕРґРєР»СЋС‡РµРЅРёСЏРјРё Рє Р±Р°Р·Рµ РґР°РЅРЅС‹С…
def get_db():
    return DATABASE_POOL.getconn()

def release_db(conn):
    DATABASE_POOL.putconn(conn)

# РРЅРёС†РёР°Р»РёР·Р°С†РёСЏ Р±Р°Р·С‹ РґР°РЅРЅС‹С… СЃ РёРЅРґРµРєСЃР°РјРё
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
                timezone TEXT DEFAULT %s
            );
            """, (DEFAULT_TIMEZONE,))
            cur.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                day INTEGER PRIMARY KEY,
                description TEXT NOT NULL
            );
            """)
            cur.execute("SELECT COUNT(*) FROM tasks")
            if cur.fetchone()['count'] == 0:
                for i, task in enumerate(TASKS, 1):
                    cur.execute("INSERT INTO tasks (day, description) VALUES (%s, %s)", (i, task))
            cur.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='timezone') THEN ALTER TABLE users ADD COLUMN timezone TEXT DEFAULT %s; END IF; END $$;", (DEFAULT_TIMEZONE,))
            # Р”РѕР±Р°РІР»РµРЅРёРµ РёРЅРґРµРєСЃРѕРІ
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_chat_id ON users (chat_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_timezone ON users (timezone);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_subscribed ON users (subscribed);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_day ON tasks (day);")
            conn.commit()
        release_db(conn)
    logging.info("РЎС…РµРјР° Р±Р°Р·С‹ РґР°РЅРЅС‹С… РёРЅРёС†РёР°Р»РёР·РёСЂРѕРІР°РЅР° СЃ РёРЅРґРµРєСЃР°РјРё.")

init_db()

# Р Р°Р±РѕС‚Р° СЃ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏРјРё
def init_user(chat_id, username=None):
    safe_username = escape(username) if username else None
    with DB_LOCK:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE chat_id = %s", (chat_id,))
                user = cur.fetchone()
                if not user:
                    cur.execute("INSERT INTO users (chat_id, username, day, timezone) VALUES (%s, %s, %s, %s)", (chat_id, safe_username, 1, DEFAULT_TIMEZONE))
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
        "subscribed", "username", "last_menu_message_id", "timezone"
    }
    safe_kwargs = {k: v for k, v in kwargs.items() if k in allowed_fields}
    if not safe_kwargs:
        logging.warning(f"update_user: РЅРµС‚ РґРѕРїСѓСЃС‚РёРјС‹С… РїРѕР»РµР№ РґР»СЏ РѕР±РЅРѕРІР»РµРЅРёСЏ {chat_id}: {list(kwargs.keys())}")
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
                    logging.warning(f"РћС€РёР±РєР° update_user РґР»СЏ {chat_id}: {e}")
            release_db(conn)

# РџРѕР»СѓС‡РµРЅРёРµ Р·Р°РґР°РЅРёСЏ РёР· Р±Р°Р·С‹ РґР°РЅРЅС‹С…
def get_task(user):
    with DB_LOCK:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                day = user.get('day') or 1
                cur.execute("SELECT description FROM tasks WHERE day = %s", (day,))
                task = cur.fetchone()
                return task['description'] if task else "Р—Р°РґР°РЅРёРµ РЅРµ РЅР°Р№РґРµРЅРѕ"
            release_db(conn)

# РџСЂРѕРІРµСЂРєР° РґРѕСЃС‚РёР¶РµРЅРёР№
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

# РџРµСЂРµС…РѕРґ Рє СЃР»РµРґСѓСЋС‰РµРјСѓ Р·Р°РґР°РЅРёСЋ
def next_task(user):
    today = datetime.now(timezone.utc).date()
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

# РћС‡РёСЃС‚РєР° РЅРµР°РєС‚РёРІРЅС‹С… РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№
def cleanup_inactive_users():
    with DB_LOCK:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                threshold = datetime.now(timezone.utc).date() - timedelta(days=90)
                cur.execute("DELETE FROM users WHERE last_done < %s", (threshold,))
                conn.commit()
                logging.info(f"РЈРґР°Р»РµРЅРѕ {cur.rowcount} РЅРµР°РєС‚РёРІРЅС‹С… РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№")
            release_db(conn)

# РРЅР»Р°Р№РЅ-РєР»Р°РІРёР°С‚СѓСЂР°
def get_inline_keyboard(user):
    keyboard = types.InlineKeyboardMarkup()
    current_day = user.get('day') or 1
    last_done = user.get('last_done')
    today = datetime.now(timezone.utc).date()
    total_days = 30

    progress = int((current_day / total_days) * 10)
    progress_bar = "[" + "в–€" * progress + " " * (10 - progress) + f"] {current_day}/{total_days}"

    can_mark_done = not last_done or last_done != today

    buttons = [
        types.InlineKeyboardButton("рџ“… РЎРµРіРѕРґРЅСЏ", callback_data="today")
    ]
    if can_mark_done:
        buttons.append(types.InlineKeyboardButton("вњ… Р’С‹РїРѕР»РЅРµРЅРѕ", callback_data="next"))
    keyboard.row(*buttons)

    keyboard.add(types.InlineKeyboardButton(f"рџ“Љ РЎС‚Р°С‚РёСЃС‚РёРєР° {progress_bar}", callback_data="stats"))
    keyboard.add(types.InlineKeyboardButton("в„№ РџРѕРјРѕС‰СЊ", callback_data="help"))
    keyboard.add(
        types.InlineKeyboardButton(
            "рџ”” РџРѕРґРїРёСЃР°С‚СЊСЃСЏ" if not user.get('subscribed', False) else "вќЊ РћС‚РїРёСЃР°С‚СЊСЃСЏ",
            callback_data="subscribe" if not user.get('subscribed', False) else "unsubscribe"
        )
    )
    keyboard.add(types.InlineKeyboardButton("рџЊђ Р§Р°СЃРѕРІРѕР№ РїРѕСЏСЃ", callback_data="set_timezone"))
    return keyboard

# РљР»Р°РІРёР°С‚СѓСЂР° РґР»СЏ РІС‹Р±РѕСЂР° С‡Р°СЃРѕРІРѕРіРѕ РїРѕСЏСЃР°
def get_timezone_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    for tz in TIMEZONES:
        keyboard.add(types.InlineKeyboardButton(tz, callback_data=f"tz_{tz}"))
    keyboard.add(types.InlineKeyboardButton("в¬… РќР°Р·Р°Рґ", callback_data="back_to_menu"))
    return keyboard

# РћС‚РїСЂР°РІРєР° СЃРѕРѕР±С‰РµРЅРёР№ СЃ РѕРіСЂР°РЅРёС‡РµРЅРёРµРј СЃРєРѕСЂРѕСЃС‚Рё
def send_message_with_rate_limit(chat_id, text, **kwargs):
    with rate_limiter:
        for attempt in range(3):
            try:
                return bot.send_message(chat_id, text, **kwargs)
            except Exception as e:
                logging.warning(f"РџРѕРІС‚РѕСЂ {attempt+1}/3: РћС€РёР±РєР° РѕС‚РїСЂР°РІРєРё СЃРѕРѕР±С‰РµРЅРёСЏ {chat_id}: {e}")
                time.sleep(2 ** attempt)
        logging.error(f"РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ СЃРѕРѕР±С‰РµРЅРёРµ {chat_id} РїРѕСЃР»Рµ РїРѕРїС‹С‚РѕРє")
        send_message_with_rate_limit(ADMIN_ID, f"вљ  РћС€РёР±РєР° РѕС‚РїСЂР°РІРєРё СЃРѕРѕР±С‰РµРЅРёСЏ РґР»СЏ {chat_id}: {e}")
        return None

# РћС‚РїСЂР°РІРєР° РјРµРЅСЋ
def send_menu(chat_id, user, text):
    try:
        fresh_user = get_user(chat_id) or user or {'subscribed': False, 'timezone': DEFAULT_TIMEZONE}
        prev_id = fresh_user.get('last_menu_message_id')
        if prev_id:
            try:
                bot.delete_message(chat_id, prev_id)
                logging.debug(f"РЈРґР°Р»РµРЅРѕ РїСЂРµРґС‹РґСѓС‰РµРµ РјРµРЅСЋ {prev_id} РґР»СЏ {chat_id}")
            except Exception:
                logging.debug(f"РќРµС‚ РїСЂРµРґС‹РґСѓС‰РµРіРѕ РјРµРЅСЋ РґР»СЏ СѓРґР°Р»РµРЅРёСЏ РІ {chat_id}")
            update_user(chat_id, last_menu_message_id=None)

        username = f"@{fresh_user.get('username')}" if fresh_user.get('username') else "РґСЂСѓРі"
        motivation = random.choice(MOTIVATIONAL_QUOTES)
        formatted_text = f"**{text}**\n\n_{motivation}_"

        msg = send_message_with_rate_limit(
            chat_id,
            formatted_text,
            parse_mode="Markdown",
            reply_markup=get_inline_keyboard(fresh_user)
        )
        if msg:
            update_user(chat_id, last_menu_message_id=msg.message_id)
        else:
            raise Exception("РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РїСЂР°РІРёС‚СЊ СЃРѕРѕР±С‰РµРЅРёРµ")
    except Exception as e:
        logging.error(f"РћС€РёР±РєР° send_menu РґР»СЏ {chat_id}: {e}")
        send_message_with_rate_limit(chat_id, "вљ  Р§С‚Рѕ-С‚Рѕ РїРѕС€Р»Рѕ РЅРµ С‚Р°Рє. РџРѕРїСЂРѕР±СѓР№ РїРѕР·Р¶Рµ!")

# РљРѕРјР°РЅРґР° /start
@bot.message_handler(commands=['start'])
def start(message):
    init_user(message.chat.id, message.from_user.username)
    user = get_user(message.chat.id)
    username = f"@{user.get('username')}" if user.get('username') else "РґСЂСѓРі"
    send_menu(
        message.chat.id,
        user,
        f"РџСЂРёРІРµС‚, {username}! рџ‘‹ РЇ С‚РІРѕР№ РЅР°СЃС‚Р°РІРЅРёРє РЅР° 30-РґРЅРµРІРЅРѕРј РїСѓС‚Рё СЂР°Р·РІРёС‚РёСЏ!\n\nРќР°Р¶РёРјР°Р№ РєРЅРѕРїРєРё РЅРёР¶Рµ, С‡С‚РѕР±С‹ РЅР°С‡Р°С‚СЊ С‡РµР»Р»РµРЅРґР¶."
    )

# РљРѕРјР°РЅРґР° /reset
@bot.message_handler(commands=['reset'])
def reset(message):
    chat_id = message.chat.id
    init_user(chat_id, message.from_user.username)
    update_user(chat_id, day=1, streak=0, last_done=None, achievements=[])
    user = get_user(chat_id)
    username = f"@{user.get('username')}" if user.get('username') else "РґСЂСѓРі"
    send_menu(
        chat_id,
        user,
        f"рџ”„ Р§РµР»Р»РµРЅРґР¶ СЃР±СЂРѕС€РµРЅ, {username}! РќР°С‡РёРЅР°РµРј СЃ РїРµСЂРІРѕРіРѕ РґРЅСЏ.\n\nрџ“Њ РЎРµРіРѕРґРЅСЏ: {get_task(user)}"
    )

# РљРѕРјР°РЅРґР° /settimezone
@bot.message_handler(commands=['settimezone'])
def set_timezone(message):
    user = get_user(message.chat.id)
    username = f"@{user.get('username')}" if user.get('username') else "РґСЂСѓРі"
    text = f"рџЊђ Р’С‹Р±РµСЂРё С‡Р°СЃРѕРІРѕР№ РїРѕСЏСЃ РґР»СЏ РЅР°РїРѕРјРёРЅР°РЅРёР№, {username} (С‚РµРєСѓС‰РёР№: {user.get('timezone', DEFAULT_TIMEZONE)}):"
    send_message_with_rate_limit(message.chat.id, text, reply_markup=get_timezone_keyboard())

# РљРѕРјР°РЅРґР° /stats
@bot.message_handler(commands=['stats'])
def stats(message):
    user = get_user(message.chat.id)
    ach_list = []
    for x in (user.get('achievements') or []):
        try:
            ach_list.append(ACHIEVEMENTS.get(int(x), ""))
        except Exception:
            pass
    ach_text = "рџЋЇ Р”РѕСЃС‚РёР¶РµРЅРёСЏ:\n" + ("\n".join(ach_list) if ach_list else "РїРѕРєР° РЅРµС‚")
    with DB_LOCK:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT COUNT(*) FROM tasks")
                total_days = cur.fetchone()['count']
            release_db(conn)
    username = f"@{user.get('username')}" if user.get('username') else "РґСЂСѓРі"
    send_menu(
        message.chat.id,
        user,
        f"рџ“Љ РЎС‚Р°С‚РёСЃС‚РёРєР°, {username}:\nрџ“… Р”РµРЅСЊ: *{user.get('day')}/{total_days}*\nрџ”Ґ РЎРµСЂРёСЏ: *{user.get('streak') or 0} РґРЅРµР№ РїРѕРґСЂСЏРґ*\nрџЊђ Р§Р°СЃРѕРІРѕР№ РїРѕСЏСЃ: *{user.get('timezone', DEFAULT_TIMEZONE)}*\n{ach_text}"
    )

# РљРѕРјР°РЅРґР° /all_stats (С‚РѕР»СЊРєРѕ РґР»СЏ Р°РґРјРёРЅР°)
@bot.message_handler(commands=['all_stats'])
def all_stats(message):
    if str(message.chat.id) != str(ADMIN_ID):
        logging.warning(f"РќРµСЃР°РЅРєС†РёРѕРЅРёСЂРѕРІР°РЅРЅР°СЏ РїРѕРїС‹С‚РєР° /all_stats РѕС‚ {message.chat.id}")
        send_message_with_rate_limit(message.chat.id, "рџљ« РљРѕРјР°РЅРґР° РґРѕСЃС‚СѓРїРЅР° С‚РѕР»СЊРєРѕ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂСѓ.")
        return

    with DB_LOCK:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT chat_id, username, day, streak, timezone FROM users ORDER BY day DESC LIMIT 500;")
                users = cur.fetchall()
            release_db(conn)

    if not users:
        send_message_with_rate_limit(message.chat.id, "РќРµС‚ РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№.")
        return

    text = "рџ‘Ґ РЎС‚Р°С‚РёСЃС‚РёРєР° РїРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏРј (РјР°РєСЃ 500):\n"
    for u in users:
        uname = f"@{u['username']}" if u.get('username') else u['chat_id']
        text += f"- {uname}: РґРµРЅСЊ {u.get('day')}, СЃРµСЂРёСЏ {u.get('streak')} РґРЅРµР№, С‡Р°СЃРѕРІРѕР№ РїРѕСЏСЃ {u.get('timezone')}\n"
    send_message_with_rate_limit(message.chat.id, text)

# РћР±СЂР°Р±РѕС‚РєР° РёРЅР»Р°Р№РЅ-РєРЅРѕРїРѕРє
@bot.callback_query_handler(func=lambda call: True)
def handle_inline_buttons(call):
    chat_id = call.message.chat.id
    current_time = time.time()
    user_key = f"{chat_id}_{call.data}"

    # РџСЂРѕРІРµСЂРєР° РЅР° СЃРїР°Рј РєРЅРѕРїРєР°РјРё
    if user_key in last_callback_time and current_time - last_callback_time[user_key] < 2:
        send_message_with_rate_limit(chat_id, "вЏі РџРѕР¶Р°Р»СѓР№СЃС‚Р°, РїРѕРґРѕР¶РґРё РЅРµРјРЅРѕРіРѕ РїРµСЂРµРґ РїРѕРІС‚РѕСЂРЅС‹Рј РЅР°Р¶Р°С‚РёРµРј.")
        bot.answer_callback_query(call.id, text="РЎР»РёС€РєРѕРј Р±С‹СЃС‚СЂРѕ! РџРѕРґРѕР¶РґРё РЅРµРјРЅРѕРіРѕ.")
        return
    last_callback_time[user_key] = current_time

    init_user(chat_id, call.from_user.username)
    user = get_user(chat_id)
    data = call.data
    username = f"@{user.get('username')}" if user.get('username') else "РґСЂСѓРі"

    # РџСЂРѕРІРµСЂРєР° РІРѕР·СЂР°СЃС‚Р° callback СЃ Р°РґР°РїС‚РёРІРЅС‹Рј РїРѕСЂРѕРіРѕРј
    try:
        callback_time = pendulum.from_timestamp(call.message.date, tz='UTC')
        request_time = pendulum.now('UTC')
        time_diff = (request_time - callback_time).total_seconds()
        callback_delays.append(time_diff)
        adaptive_threshold = max(mean(callback_delays) + 5, 15) if callback_delays else 15
        logging.info(f"Callback РѕС‚ {chat_id}: {data}, РІРѕР·СЂР°СЃС‚ {time_diff:.2f} СЃРµРє, РїРѕСЂРѕРі {adaptive_threshold:.2f} СЃРµРє")
        if time_diff >= adaptive_threshold:
            logging.info(f"РџСЂРѕРїСѓС‰РµРЅ СѓСЃС‚Р°СЂРµРІС€РёР№ callback РѕС‚ {chat_id}: {data}, РІРѕР·СЂР°СЃС‚ {time_diff} СЃРµРєСѓРЅРґ")
            bot.answer_callback_query(call.id, text="Р—Р°РїСЂРѕСЃ СѓСЃС‚Р°СЂРµР», РѕС‚РїСЂР°РІР»СЏСЋ РЅРѕРІРѕРµ РјРµРЅСЋ.")
            send_menu(chat_id, user, f"рџ“Њ РЎРµРіРѕРґРЅСЏ, {username}:\n{get_task(user)}\n\nрџ•’ Р§Р°СЃРѕРІРѕР№ РїРѕСЏСЃ: *{user.get('timezone', DEFAULT_TIMEZONE)}*")
            return
        bot.answer_callback_query(call.id)
    except Exception as e:
        logging.warning(f"РћС€РёР±РєР° РїСЂРѕРІРµСЂРєРё callback РґР»СЏ {chat_id}: {e}")
        bot.answer_callback_query(call.id, text="РџСЂРѕРёР·РѕС€Р»Р° РѕС€РёР±РєР°, РїРѕРїСЂРѕР±СѓР№ СЃРЅРѕРІР°.")
        return

    if data == "today":
        send_menu(chat_id, user, f"рџ“Њ РЎРµРіРѕРґРЅСЏ, {username}:\n{get_task(user)}\n\nрџ•’ Р§Р°СЃРѕРІРѕР№ РїРѕСЏСЃ: *{user.get('timezone', DEFAULT_TIMEZONE)}*")
        send_message_with_rate_limit(chat_id, "вњ… Р—Р°РґР°РЅРёРµ РїРѕРєР°Р·Р°РЅРѕ!")

    elif data == "next":
        task, achievements, user = next_task(user)
        with DB_LOCK:
            with get_db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT COUNT(*) FROM tasks")
                    total_days = cur.fetchone()['count']
                release_db(conn)
        text = f"вћЎпёЏ РЎР»РµРґСѓСЋС‰РµРµ Р·Р°РґР°РЅРёРµ, {username}!\n{task}\n\nрџ”Ґ РЎРµСЂРёСЏ: *{user.get('streak')} РґРЅРµР№*\nрџ“… Р”РµРЅСЊ *{user.get('day')}/{total_days}*"
        send_menu(chat_id, user, text)
        for ach in achievements:
            send_message_with_rate_limit(
                chat_id,
                f"рџЋ‰ {ach}\n\n_{random.choice(MOTIVATIONAL_QUOTES)}_",
                parse_mode="Markdown"
            )

    elif data == "stats":
        ach_list = []
        for x in (user.get('achievements') or []):
            try:
                ach_list.append(ACHIEVEMENTS.get(int(x), ""))
            except Exception:
                pass
        ach_text = "рџЋЇ Р”РѕСЃС‚РёР¶РµРЅРёСЏ:\n" + ("\n".join(ach_list) if ach_list else "РїРѕРєР° РЅРµС‚")
        with DB_LOCK:
            with get_db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT COUNT(*) FROM tasks")
                    total_days = cur.fetchone()['count']
                release_db(conn)
        send_menu(
            chat_id,
            user,
            f"рџ“Љ РЎС‚Р°С‚РёСЃС‚РёРєР°, {username}:\nрџ“… Р”РµРЅСЊ: *{user.get('day')}/{total_days}*\nрџ”Ґ РЎРµСЂРёСЏ: *{user.get('streak') or 0} РґРЅРµР№ РїРѕРґСЂСЏРґ*\nрџЊђ Р§Р°СЃРѕРІРѕР№ РїРѕСЏСЃ: *{user.get('timezone', DEFAULT_TIMEZONE)}*\n{ach_text}"
        )

    elif data == "subscribe":
        update_user(chat_id, subscribed=True)
        user = get_user(chat_id)
        send_menu(
            chat_id,
            user,
            f"вњ… РќР°РїРѕРјРёРЅР°РЅРёСЏ РІРєР»СЋС‡РµРЅС‹, {username}! Р‘СѓРґСѓ РїРёСЃР°С‚СЊ РІ {REMINDER_HOUR} РїРѕ С‚РІРѕРµРјСѓ С‡Р°СЃРѕРІРѕРјСѓ РїРѕСЏСЃСѓ (*{user.get('timezone', DEFAULT_TIMEZONE)}*)."
        )
        update_scheduler()  # РћР±РЅРѕРІР»СЏРµРј СЂР°СЃРїРёСЃР°РЅРёРµ РїСЂРё РїРѕРґРїРёСЃРєРµ

    elif data == "unsubscribe":
        update_user(chat_id, subscribed=False)
        user = get_user(chat_id)
        send_menu(chat_id, user, f"вќЊ РўС‹ РѕС‚РїРёСЃР°Р»СЃСЏ РѕС‚ РЅР°РїРѕРјРёРЅР°РЅРёР№, {username}.")
        update_scheduler()  # РћР±РЅРѕРІР»СЏРµРј СЂР°СЃРїРёСЃР°РЅРёРµ РїСЂРё РѕС‚РїРёСЃРєРµ

    elif data == "help":
        send_menu(
            chat_id,
            user,
            f"в„№ РџРѕРјРѕС‰СЊ, {username}:\n"
            "рџ“… вЂ” РїРѕРєР°Р·Р°С‚СЊ Р·Р°РґР°РЅРёРµ РЅР° СЃРµРіРѕРґРЅСЏ\n"
            "вњ… вЂ” РѕС‚РјРµС‚РёС‚СЊ РІС‹РїРѕР»РЅРµРЅРёРµ\n"
            "рџ“Љ вЂ” СЃС‚Р°С‚РёСЃС‚РёРєР°\n"
            "рџ”” вЂ” РїРѕРґРїРёСЃРєР° РЅР° РЅР°РїРѕРјРёРЅР°РЅРёСЏ\n"
            "рџЊђ вЂ” РЅР°СЃС‚СЂРѕР№РєР° С‡Р°СЃРѕРІРѕРіРѕ РїРѕСЏСЃР°\n"
            "/reset вЂ” СЃР±СЂРѕСЃРёС‚СЊ РїСЂРѕРіСЂРµСЃСЃ\n"
            "/settimezone вЂ” РІС‹Р±СЂР°С‚СЊ С‡Р°СЃРѕРІРѕР№ РїРѕСЏСЃ\n\n"
            "рџЋЇ Р’С‹РїРѕР»РЅСЏСЏ Р·Р°РґР°РЅРёСЏ РїРѕРґСЂСЏРґ, С‚С‹ Р±СѓРґРµС€СЊ РїРѕР»СѓС‡Р°С‚СЊ РґРѕСЃС‚РёР¶РµРЅРёСЏ!"
        )

    elif data == "set_timezone":
        text = f"рџЊђ Р’С‹Р±РµСЂРё С‡Р°СЃРѕРІРѕР№ РїРѕСЏСЃ РґР»СЏ РЅР°РїРѕРјРёРЅР°РЅРёР№, {username} (С‚РµРєСѓС‰РёР№: {user.get('timezone', DEFAULT_TIMEZONE)}):"
        send_message_with_rate_limit(chat_id, text, reply_markup=get_timezone_keyboard())

    elif data.startswith("tz_"):
        new_timezone = data[3:]
        if new_timezone in TIMEZONES:
            update_user(chat_id, timezone=new_timezone)
            user = get_user(chat_id)
            send_menu(
                chat_id,
                user,
                f"рџЊђ Р§Р°СЃРѕРІРѕР№ РїРѕСЏСЃ СѓСЃС‚Р°РЅРѕРІР»РµРЅ: *{new_timezone}*\n\nРќР°РїРѕРјРёРЅР°РЅРёСЏ Р±СѓРґСѓС‚ РїСЂРёС…РѕРґРёС‚СЊ РІ {REMINDER_HOUR} РїРѕ С‚РІРѕРµРјСѓ РІСЂРµРјРµРЅРё."
            )
            update_scheduler()  # РћР±РЅРѕРІР»СЏРµРј СЂР°СЃРїРёСЃР°РЅРёРµ РїСЂРё СЃРјРµРЅРµ С‡Р°СЃРѕРІРѕРіРѕ РїРѕСЏСЃР°
        else:
            send_message_with_rate_limit(chat_id, "вљ  РќРµРІРµСЂРЅС‹Р№ С‡Р°СЃРѕРІРѕР№ РїРѕСЏСЃ. РџРѕРїСЂРѕР±СѓР№ СЃРЅРѕРІР°.")

    elif data == "back_to_menu":
        send_menu(chat_id, user, f"рџ“Њ РЎРµРіРѕРґРЅСЏ, {username}:\n{get_task(user)}\n\nрџ•’ Р§Р°СЃРѕРІРѕР№ РїРѕСЏСЃ: *{user.get('timezone', DEFAULT_TIMEZONE)}*")

# РџР»Р°РЅРёСЂРѕРІС‰РёРє РЅР°РїРѕРјРёРЅР°РЅРёР№ СЃ APScheduler
scheduler = BackgroundScheduler()

def send_scheduled_task_for_tz(tz):
    with DB_LOCK:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE subscribed = TRUE AND timezone = %s;", (tz,))
                subs = cur.fetchall()
            release_db(conn)

    for user in subs:
        try:
            now = pendulum.now(tz)
            task = get_task(user)
            username = f"@{user.get('username')}" if user.get('username') else "РґСЂСѓРі"
            send_message_with_rate_limit(
                user['chat_id'],
                f"рџ“Њ РќР°РїРѕРјРёРЅР°РЅРёРµ, {username} ({now.to_formatted_date_string()}):\n{task}\n\n_{random.choice(MOTIVATIONAL_QUOTES)}_",
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"РћС€РёР±РєР° РІ РЅР°РїРѕРјРёРЅР°РЅРёРё РґР»СЏ {user['chat_id']} РІ {tz}: {e}")
            send_message_with_rate_limit(ADMIN_ID, f"вљ  РћС€РёР±РєР° РІ РЅР°РїРѕРјРёРЅР°РЅРёРё РґР»СЏ {user['chat_id']}: {e}")

def update_scheduler():
    with DB_LOCK:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT DISTINCT timezone FROM users WHERE subscribed = TRUE;")
                timezones = [row['timezone'] for row in cur.fetchall()]
            release_db(conn)
    
    scheduler.remove_all_jobs()
    hour, minute = map(int, REMINDER_HOUR.split(':'))
    for tz in timezones:
        scheduler.add_job(
            send_scheduled_task_for_tz,
            CronTrigger(hour=hour, minute=minute, timezone=tz),
            args=[tz]
        )
    logging.info(f"РћР±РЅРѕРІР»РµРЅРѕ СЂР°СЃРїРёСЃР°РЅРёРµ РґР»СЏ {len(timezones)} С‡Р°СЃРѕРІС‹С… РїРѕСЏСЃРѕРІ")

# Р’РµР±С…СѓРє-СЃРµСЂРІРµСЂ
@app.route('/webhook', methods=['POST'])
def webhook():
    body = request.get_data(as_text=True)
    try:
        update = telebot.types.Update.de_json(body)
        if update.message:
            user = update.message.from_user
            logging.info(f"рџ“© РЎРѕРѕР±С‰РµРЅРёРµ РѕС‚ @{user.username or user.id}: {getattr(update.message, 'text', '')}")
        elif update.callback_query:
            user = update.callback_query.from_user
            logging.info(f"рџ” Callback РѕС‚ @{user.username or user.id}: {update.callback_query.data}")
        bot.process_new_updates([update])
        return '', 200
    except Exception as e:
        logging.error(f"РћС€РёР±РєР° РѕР±СЂР°Р±РѕС‚РєРё РІРµР±С…СѓРєР°: {e}")
        send_message_with_rate_limit(ADMIN_ID, f"вљ  РћС€РёР±РєР° РІРµР±С…СѓРєР°: {e}")
        return '', 400

@app.route('/')
def index():
    return "РџСЂРёРІРµС‚, СЏ Р¶РёРІ!", 200

# Р—Р°РїСѓСЃРє
if __name__ == '__main__':
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    logging.info(f"рџ”— Р’РµР±С…СѓРє СѓСЃС‚Р°РЅРѕРІР»РµРЅ: {WEBHOOK_URL}")

    schedule.every().week.do(cleanup_inactive_users)
    threading.Thread(target=schedule_checker, daemon=True).start()

    update_scheduler()
    scheduler.start()

    # Gunicorn Р·Р°РїСѓСЃРєР°РµС‚СЃСЏ РѕС‚РґРµР»СЊРЅРѕ, РїРѕСЌС‚РѕРјСѓ СѓР±РёСЂР°РµРј app.run()
    # port = int(os.getenv("PORT", 10000))
    # app.run(host='0.0.0.0', port=port)
