import os
import json
import telebot
import schedule
import time
import threading
import logging
from telebot import types
from datetime import datetime, timedelta
import http.server
import socketserver

# 🔑 Логирование
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 🔑 Токен
TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables.")
bot = telebot.TeleBot(TOKEN)

# 🌍 Render hostname (для вебхука)
HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
if not HOSTNAME:
    raise RuntimeError("RENDER_EXTERNAL_HOSTNAME is not set.")
WEBHOOK_URL = f"https://{HOSTNAME}/webhook"

# 📚 30-дневная программа
tasks = [
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

# 📂 Файлы прогресса и подписок
PROGRESS_FILE = "progress.json"
SUBSCRIBERS_FILE = "subscribers.json"

def load_json(filename):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

user_progress = load_json(PROGRESS_FILE)
subscribers = load_json(SUBSCRIBERS_FILE)

# 🏆 Достижения
ACHIEVEMENTS = {
    5: "🏅 Молодец! 5 дней подряд!",
    10: "🥈 Ты машина! 10 дней без перерыва!",
    20: "🥇 Железная сила воли! 20 дней подряд!",
    30: "👑 Герой челленджа! 30 дней!"
}

# 📌 Инициализация пользователя
def init_user(chat_id):
    chat_id = str(chat_id)
    if chat_id not in user_progress:
        user_progress[chat_id] = {"day": 0, "streak": 0, "last_done": "", "achievements": []}
        save_json(PROGRESS_FILE, user_progress)

# 🔄 Получить задание
def get_task(chat_id):
    chat_id = str(chat_id)
    day = user_progress[chat_id]["day"]
    if day < len(tasks):
        return tasks[day]
    return "🎉 Программа завершена! Ты прошёл 30 дней 🚀"

# 🎯 Проверка достижений
def check_achievements(chat_id):
    chat_id = str(chat_id)
    streak = user_progress[chat_id]["streak"]
    unlocked = []
    for threshold, text in ACHIEVEMENTS.items():
        if streak >= threshold and threshold not in user_progress[chat_id]["achievements"]:
            user_progress[chat_id]["achievements"].append(threshold)
            unlocked.append(text)
    if unlocked:
        save_json(PROGRESS_FILE, user_progress)
    return unlocked

# ⏩ Следующее задание
def next_task(chat_id):
    chat_id = str(chat_id)
    today = datetime.now().date()
    last_done_str = user_progress[chat_id].get("last_done", "")
    if last_done_str:
        try:
            last_done = datetime.strptime(last_done_str, "%Y-%m-%d").date()
        except Exception:
            last_done = None
        if last_done and today == last_done + timedelta(days=1):
            user_progress[chat_id]["streak"] += 1
        elif last_done == today:
            pass
        else:
            user_progress[chat_id]["streak"] = 1
    else:
        user_progress[chat_id]["streak"] = 1

    user_progress[chat_id]["last_done"] = str(today)
    if user_progress[chat_id]["day"] < len(tasks):
        user_progress[chat_id]["day"] += 1
    save_json(PROGRESS_FILE, user_progress)
    return get_task(chat_id), check_achievements(chat_id)

# 🖲 Кнопки
def get_inline_keyboard(chat_id):
    subscribed = str(chat_id) in subscribers
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("📅 Сегодняшнее задание", callback_data="today"))
    keyboard.add(types.InlineKeyboardButton("✅ Выполнено → Следующее", callback_data="next"))
    keyboard.add(types.InlineKeyboardButton("📊 Статистика", callback_data="stats"))
    if subscribed:
        keyboard.add(types.InlineKeyboardButton("❌ Отписаться", callback_data="unsubscribe"))
    else:
        keyboard.add(types.InlineKeyboardButton("🔔 Подписаться (09:00)", callback_data="subscribe"))
    keyboard.add(types.InlineKeyboardButton("ℹ Помощь", callback_data="help"))
    return keyboard

# 🚀 /start
@bot.message_handler(commands=['start'])
def start(message):
    init_user(message.chat.id)
    bot.send_message(
        message.chat.id,
        "Привет 👋 Я твой наставник на 30-дневном пути развития!\n\n"
        "Нажимай кнопки ниже, чтобы получать задания и отмечать выполнение.",
        reply_markup=get_inline_keyboard(message.chat.id)
    )

# 🎛 Обработка кнопок
@bot.callback_query_handler(func=lambda call: True)
def handle_inline_buttons(call):
    chat_id = str(call.message.chat.id)
    init_user(chat_id)
    data = call.data

    try:
        bot.answer_callback_query(call.id)
    except Exception as e:
        logging.warning(f"Callback error: {e}")

    if data == "today":
        bot.send_message(call.message.chat.id, f"📌 Сегодня: {get_task(chat_id)}", reply_markup=get_inline_keyboard(chat_id))

    elif data == "next":
        task, achievements = next_task(chat_id)
        streak = user_progress[chat_id]["streak"]
        day = user_progress[chat_id]["day"]
        text = f"➡ Следующее задание:\n{task}\n\n🔥 Серия: {streak} дней\n📅 День {day}/{len(tasks)}"
        bot.send_message(call.message.chat.id, text, reply_markup=get_inline_keyboard(chat_id))
        for ach in achievements:
            bot.send_message(call.message.chat.id, f"🎉 {ach}")

    elif data == "stats":
        streak = user_progress[chat_id]["streak"]
        day = user_progress[chat_id]["day"]
        ach_list = [ACHIEVEMENTS[x].split(" ")[0] for x in user_progress[chat_id]["achievements"] if x in ACHIEVEMENTS]
        ach_text = "🎯 Достижения: " + (" ".join(ach_list) if ach_list else "пока нет")
        bot.send_message(
            call.message.chat.id,
            f"📊 Статистика:\n📅 День: {day}/{len(tasks)}\n🔥 Серия: {streak} дней подряд\n{ach_text}",
            reply_markup=get_inline_keyboard(chat_id)
        )

    elif data == "subscribe":
        subscribers[chat_id] = True
        save_json(SUBSCRIBERS_FILE, subscribers)
        bot.send_message(call.message.chat.id, "✅ Напоминания включены! Буду писать в 09:00 каждый день.", reply_markup=get_inline_keyboard(chat_id))

    elif data == "unsubscribe":
        if chat_id in subscribers:
            del subscribers[chat_id]
            save_json(SUBSCRIBERS_FILE, subscribers)
        bot.send_message(call.message.chat.id, "❌ Ты отписался от напоминаний.", reply_markup=get_inline_keyboard(chat_id))

    elif data == "help":
        bot.send_message(
            call.message.chat.id,
            "ℹ Я помогаю пройти 30-дневную программу совершенствования:\n"
            "📅 — показать задание на сегодня\n"
            "✅ — отметить выполнение\n"
            "📊 — статистика\n"
            "🔔 — подписка на напоминания (09:00)\n\n"
            "🎯 Выполняя задания подряд, ты будешь получать достижения!",
            reply_markup=get_inline_keyboard(chat_id)
        )

# ⏰ Планировщик (только подписчикам)
def schedule_checker():
    while True:
        schedule.run_pending()
        time.sleep(30)

def send_scheduled_task():
    for chat_id in list(subscribers.keys()):
        try:
            task, achievements = next_task(chat_id)
            streak = user_progress[str(chat_id)]["streak"]
            day = user_progress[str(chat_id)]["day"]
            text = f"📌 Автоматическое напоминание:\n{task}\n\n🔥 Серия: {streak} дней\n📅 День {day}/{len(tasks)}"
            bot.send_message(int(chat_id), text, reply_markup=get_inline_keyboard(chat_id))
            for ach in achievements:
                bot.send_message(int(chat_id), f"🎉 {ach}")
        except Exception as e:
            logging.error(f"Error in scheduled task for {chat_id}: {e}")

# 🌍 Webhook сервер
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_POST(self):
        if self.path == "/webhook":
            length = int(self.headers['content-length'])
            body = self.rfile.read(length)
            update = telebot.types.Update.de_json(body.decode("utf-8"))
            bot.process_new_updates([update])
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

def start_web_server():
    port = int(os.getenv("PORT", 10000))
    with ReusableTCPServer(("", port), Handler) as httpd:
        logging.info(f"✅ Webhook server running on port {port}")
        httpd.serve_forever()

# ▶️ Запуск
if __name__ == '__main__':
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    logging.info(f"🔗 Webhook установлен: {WEBHOOK_URL}")

    schedule.every().day.at("09:00").do(send_scheduled_task)
    threading.Thread(target=schedule_checker, daemon=True).start()

    start_web_server()
