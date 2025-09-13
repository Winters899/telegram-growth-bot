import os
import json
import telebot
import schedule
import time
import threading
import http.server
import socketserver
from telebot import types
from datetime import datetime, timedelta

# 🔑 Получаем токен из переменных окружения (Render)
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is not set in environment variables. Set it on Render or locally before running.")

bot = telebot.TeleBot(TOKEN)

# 📚 30-дневная программа
tasks = [
    # Неделя 1
    "День 1: Определи 10 ключевых целей на ближайший год.",
    "День 2: Составь утренний ритуал (вода, зарядка, визуализация).",
    "День 3: Откажись от одной вредной привычки.",
    "День 4: Веди дневник мыслей и благодарностей.",
    "День 5: Составь список из 10 сильных сторон.",
    "День 6: Сделай цифровой детокс на 6 часов.",
    "День 7: Подведи итоги недели, отметь победы.",
    # Неделя 2
    "День 8: Применяй правило Парето 20/80.",
    "День 9: Определи 3 главные приоритеты дня.",
    "День 10: Используй технику Pomodoro (25/5).",
    "День 11: Наведи порядок на рабочем месте.",
    "День 12: Минимизируй отвлекающие факторы (уведомления, вкладки).",
    "День 13: Сделай 2 часа глубокой работы без отвлечений.",
    "День 14: Итоги недели: оцени продуктивность.",
    # Неделя 3
    "День 15: Напиши свою миссию и ценности.",
    "День 16: Практикуй публичные мини-выступления (или запись на камеру).",
    "День 17: Научись говорить «нет» — установи границы.",
    "День 18: Прочитай биографию выдающегося лидера.",
    "День 19: Сделай доброе дело (помоги или поддержи).",
    "День 20: Визуализируй идеальную версию себя через 5 лет.",
    "День 21: Итоги недели: оцени уверенность и влияние.",
    # Неделя 4
    "День 22: Составь план учёбы на 1 год (книги, курсы, навыки).",
    "День 23: Определи наставника или источник вдохновения.",
    "День 24: Практикуй вечерний анализ дня (рефлексия).",
    "День 25: Составь финансовую стратегию (доходы и инвестиции).",
    "День 26: Сделай ревизию окружения (люди, инфопоток).",
    "День 27: Поделись знаниями с кем-то (mini-лекция/пост).",
    "День 28: Итоги: составь план на следующий месяц.",
    "День 29: Определи 3 долгосрочные мечты (10 лет).",
    "День 30: Создай карту жизни с целями и стратегией."
]

# 📂 Файл для прогресса
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

# 🔄 Получить задание для пользователя
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

# ⏩ Отметить выполнение и перейти к следующему заданию
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

# 🖲 Inline-кнопки
def get_inline_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    btn_today = types.InlineKeyboardButton("📅 Сегодняшнее задание", callback_data="today")
    btn_done = types.InlineKeyboardButton("✅ Выполнено → Следующее", callback_data="next")
    btn_stats = types.InlineKeyboardButton("📊 Статистика", callback_data="stats")
    btn_sub = types.InlineKeyboardButton("🔔 Подписаться (09:00)", callback_data="subscribe")
    btn_help = types.InlineKeyboardButton("ℹ Помощь", callback_data="help")
    keyboard.add(btn_today)
    keyboard.add(btn_done)
    keyboard.add(btn_stats, btn_sub)
    keyboard.add(btn_help)
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

# 🎛 Обработка inline-кнопок
@bot.callback_query_handler(func=lambda call: True)
def handle_inline_buttons(call):
    chat_id = str(call.message.chat.id)
    init_user(chat_id)
    data = call.data

    if data == "today":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, f"📌 Сегодня: {get_task(chat_id)}", reply_markup=get_inline_keyboard())

    elif data == "next":
        bot.answer_callback_query(call.id)
        task, achievements = next_task(chat_id)
        streak = user_progress[chat_id]["streak"]
        day = user_progress[chat_id]["day"]
        text = f"➡ Следующее задание:\n{task}\n\n🔥 Серия: {streak} дней\n📅 День {day}/{len(tasks)}"
        bot.send_message(call.message.chat.id, text, reply_markup=get_inline_keyboard())
        for ach in achievements:
            bot.send_message(call.message.chat.id, f"🎉 {ach}")

    elif data == "stats":
        bot.answer_callback_query(call.id)
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
        bot.answer_callback_query(call.id)
        init_user(chat_id)
        threading.Thread(target=schedule_checker, args=(int(chat_id),), daemon=True).start()
        bot.send_message(call.message.chat.id, "✅ Напоминания включены! Буду писать в 09:00 каждый день.", reply_markup=get_inline_keyboard())

    elif data == "help":
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "ℹ Я помогаю пройти 30-дневную программу совершенствования:\n"
            "📅 — показать задание на сегодня\n"
            "✅ — отметить выполнение и перейти к следующему дню\n"
            "📊 — показать статистику\n"
            "🔔 — включить ежедневные напоминания в 09:00\n\n"
            "🎯 Выполняя задания подряд, ты будешь получать достижения и эмодзи-медали!",
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

# 🌐 HTTP-сервер с healthcheck
class HealthHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/ping":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

def start_web_server():
    port = int(os.getenv("PORT", 10000))
    with socketserver.TCPServer(("", port), HealthHandler) as httpd:
        print(f"✅ Web server running on port {port}")
        httpd.serve_forever()

# ▶️ Запуск бота и сервера
if __name__ == '__main__':
    threading.Thread(target=start_web_server, daemon=True).start()
    try:
        bot.polling(non_stop=True)
    except KeyboardInterrupt:
        print("Stopping bot...")
