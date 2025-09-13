import os
import json
import telebot
import schedule
import time
import threading
from telebot import types
from datetime import datetime, timedelta
import http.server
import socketserver

# 🔑 Токен из Render переменных окружения
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is not set in environment variables.")

bot = telebot.TeleBot(TOKEN)

# 🌍 Домен Render (нужен для webhook)
HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
if not HOSTNAME:
    raise RuntimeError("RENDER_EXTERNAL_HOSTNAME is not set (Render автоматически задаёт).")

WEBHOOK_URL = f"https://{HOSTNAME}/"

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

# 📂 Файл прогресса
PROGRESS_FILE = "progress.json"

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_progress():
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(user_progress, f, ensure_ascii=False, indent=2)

user_progress = load_progress()

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
        user_progress[chat_id] = {
            "day": 0,
            "streak": 0,
            "last_done": "",
            "achievements": []
        }
        save_progress()

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
        save_progress()
    return unlocked

# ⏩ Выполнение и переход к следующему
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
    save_progress()
    return get_task(chat_id), check_achievements(chat_id)

# 🖲 Кнопки
def get_inline_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("📅 Сегодняшнее задание", callback_data="today"))
    keyboard.add(types.InlineKeyboardButton("✅ Выполнено → Следующее", callback_data="next"))
    keyboard.add(types.InlineKeyboardButton("📊 Статистика", callback_data="stats"),
                 types.InlineKeyboardButton("🔔 Подписаться (09:00)", callback_data="subscribe"))
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
        reply_markup=get_inline_keyboard()
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
        print(f"Callback error: {e}")

    if data == "today":
        bot.send_message(call.message.chat.id, f"📌 Сегодня: {get_task(chat_id)}", reply_markup=get_inline_keyboard())

    elif data == "next":
        task, achievements = next_task(chat_id)
        streak = user_progress[chat_id]["streak"]
        day = user_progress[chat_id]["day"]
        text = f"➡ Следующее задание:\n{task}\n\n🔥 Серия: {streak} дней\n📅 День {day}/{len(tasks)}"
        bot.send_message(call.message.chat.id, text, reply_markup=get_inline_keyboard())
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
            reply_markup=get_inline_keyboard()
        )

    elif data == "subscribe":
        threading.Thread(target=schedule_checker, args=(int(chat_id),), daemon=True).start()
        bot.send_message(call.message.chat.id, "✅ Напоминания включены! Буду писать в 09:00 каждый день.", reply_markup=get_inline_keyboard())

    elif data == "help":
        bot.send_message(
            call.message.chat.id,
            "ℹ Я помогаю пройти 30-дневную программу совершенствования:\n"
            "📅 — показать задание на сегодня\n"
            "✅ — отметить выполнение\n"
            "📊 — статистика\n"
            "🔔 — напоминания в 09:00\n\n"
            "🎯 Выполняя задания подряд, ты будешь получать достижения!",
            reply_markup=get_inline_keyboard()
        )

# ⏰ Планировщик
def schedule_checker(chat_id):
    schedule.every().day.at("09:00").do(lambda: send_scheduled_task(chat_id))
    while True:
        schedule.run_pending()
        time.sleep(30)

def send_scheduled_task(chat_id):
    try:
        task, achievements = next_task(chat_id)
        streak = user_progress[str(chat_id)]["streak"]
        day = user_progress[str(chat_id)]["day"]
        text = f"📌 Автоматическое напоминание:\n{task}\n\n🔥 Серия: {streak} дней\n📅 День {day}/{len(tasks)}"
        bot.send_message(chat_id, text, reply_markup=get_inline_keyboard())
        for ach in achievements:
            bot.send_message(chat_id, f"🎉 {ach}")
    except Exception as e:
        print(f"Error in scheduled task for {chat_id}: {e}")

# 🌍 Webhook сервер для Render
class Handler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_POST(self):
        length = int(self.headers['content-length'])
        body = self.rfile.read(length)
        update = telebot.types.Update.de_json(body.decode("utf-8"))
        bot.process_new_updates([update])
        self.send_response(200)
        self.end_headers()

# сервер с allow_reuse_address
class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

def start_web_server():
    port = int(os.getenv("PORT", 10000))
    with ReusableTCPServer(("", port), Handler) as httpd:
        print(f"✅ Webhook server running on port {port}")
        httpd.serve_forever()

# ▶️ Запуск
if __name__ == '__main__':
    # Настраиваем webhook
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    print(f"🔗 Webhook установлен: {WEBHOOK_URL}")

    # Запуск сервера
    start_web_server()
