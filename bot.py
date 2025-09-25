import os
import random
import logging
import json
from datetime import date, datetime

import telebot
from telebot import types
from flask import Flask, request

# -------------------------
# Настройки
# -------------------------
TOKEN = os.environ.get("TELEGRAM_TOKEN")
APP_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")

if not TOKEN or not APP_URL:
    raise ValueError("❌ TELEGRAM_TOKEN и WEBHOOK_URL должны быть заданы!")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

STATE_FILE = "bot_state.json"
MAX_DAYS_INACTIVE = 30  # число дней для хранения данных пользователя

# -------------------------
# Логирование
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# -------------------------
# Загрузка советов
# -------------------------
try:
    with open("phrases.txt", "r", encoding="utf-8") as f:
        phrases = [p.strip() for p in f.read().split('---') if p.strip()]
except FileNotFoundError:
    phrases = []

if not phrases:
    phrases = ["Советы не найдены. Добавь файл phrases.txt с советами через '---'"]

logging.info(f"Загружено {len(phrases)} советов")

# -------------------------
# Хранилище
# -------------------------
daily_phrase = {}       # {chat_id: {"date": "YYYY-MM-DD", "phrase": "..."}}
last_phrase = {}        # {chat_id: "последняя фраза"}
random_index = {}       # {chat_id: индекс следующей фразы}
shuffled_phrases = {}   # {chat_id: [список фраз в случайном порядке]}

# -------------------------
# Сохранение и загрузка состояния
# -------------------------
def save_state():
    state = {
        "daily_phrase": daily_phrase,
        "last_phrase": last_phrase,
        "random_index": random_index,
        "shuffled_phrases": shuffled_phrases
    }
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as e:
        logging.warning(f"Не удалось сохранить состояние: {e}")

def load_state():
    global daily_phrase, last_phrase, random_index, shuffled_phrases
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
            daily_phrase = state.get("daily_phrase", {})
            last_phrase = state.get("last_phrase", {})
            random_index = state.get("random_index", {})
            shuffled_phrases = state.get("shuffled_phrases", {})
            logging.info("✅ Состояние загружено")
    except FileNotFoundError:
        logging.info("Файл состояния не найден, создаем новый")
    except Exception as e:
        logging.warning(f"Не удалось загрузить состояние: {e}")

load_state()

# -------------------------
# Очистка старых пользователей
# -------------------------
def cleanup_old_users():
    today = date.today()
    removed_users = []

    for chat_id in list(daily_phrase.keys()):
        user_date_str = daily_phrase[chat_id].get("date")
        if user_date_str:
            user_date = datetime.strptime(user_date_str, "%Y-%m-%d").date()
            if (today - user_date).days > MAX_DAYS_INACTIVE:
                daily_phrase.pop(chat_id, None)
                last_phrase.pop(chat_id, None)
                random_index.pop(chat_id, None)
                shuffled_phrases.pop(chat_id, None)
                removed_users.append(str(chat_id))

    if removed_users:
        logging.info(f"🗑 Очищены данные пользователей: {', '.join(removed_users)}")
        save_state()

# -------------------------
# Функции
# -------------------------
def get_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("📅 Совет дня", callback_data="daily"),
        types.InlineKeyboardButton("💡 Новый совет", callback_data="random"),
    )
    return kb

def get_daily_phrase(chat_id: int) -> str:
    cleanup_old_users()
    today_str = str(date.today())
    record = daily_phrase.get(str(chat_id))

    if not record or record.get("date") != today_str:
        yesterday_phrase = record["phrase"] if record else None
        available = [p for p in phrases if p != yesterday_phrase]
        phrase = random.choice(available or phrases)
        daily_phrase[str(chat_id)] = {"date": today_str, "phrase": phrase}
        save_state()
    return daily_phrase[str(chat_id)]["phrase"]

def get_random_phrase(chat_id: int) -> str:
    cleanup_old_users()
    chat_id_str = str(chat_id)

    if chat_id_str not in shuffled_phrases or not shuffled_phrases[chat_id_str]:
        shuffled = phrases[:]
        random.shuffle(shuffled)
        shuffled_phrases[chat_id_str] = shuffled
        random_index[chat_id_str] = 0

    idx = random_index[chat_id_str]
    phrase = shuffled_phrases[chat_id_str][idx]

    random_index[chat_id_str] += 1
    if random_index[chat_id_str] >= len(shuffled_phrases[chat_id_str]):
        shuffled = phrases[:]
        random.shuffle(shuffled)
        shuffled_phrases[chat_id_str] = shuffled
        random_index[chat_id_str] = 0

    save_state()
    return phrase

def decorate_phrase(phrase: str) -> str:
    emojis = ["✨", "⭐", "🌟", "💎", "🔥", "💡", "🌱", "📌", "🔑", "🚀"]
    emoji = random.choice(emojis)
    return f"{phrase} {emoji}"

def send_or_edit(c, new_text: str):
    kb = get_keyboard()
    old_text = c.message.text or ""

    if new_text.strip() == old_text.strip() and c.message.reply_markup == kb:
        logging.debug("Редактирование пропущено: текст и клавиатура совпадают")
        return

    try:
        bot.edit_message_text(
            chat_id=c.message.chat.id,
            message_id=c.message.message_id,
            text=new_text,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logging.warning(f"Не удалось отредактировать сообщение: {e}")

# -------------------------
# Хэндлеры
# -------------------------
@bot.message_handler(commands=["start"])
def start_msg(message):
    logging.info(f"/start от {message.chat.id}")
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass
    bot.send_message(
        message.chat.id,
        "Привет! Я бот советов 🌞\n\nВыбери опцию:",
        reply_markup=get_keyboard(),
    )

@bot.callback_query_handler(func=lambda c: True)
def callback_inline(c):
    if c.data == "daily":
        phrase = get_daily_phrase(c.message.chat.id)
        phrase = decorate_phrase(phrase)
        today_str = datetime.now().strftime("%d.%m.%Y")
        text = f"🗓💡 <b>Совет на сегодня ({today_str}):</b>\n\n{phrase}"
        bot.answer_callback_query(c.id, "Сегодняшний совет ✅", show_alert=False)

    elif c.data == "random":
        phrase = get_random_phrase(c.message.chat.id)
        phrase = decorate_phrase(phrase)
        headers = ["✨", "🌟", "🔥", "🚀", "⭐", "💎"]
        header = random.choice(headers)
        text = f"{header} <b>Новый совет:</b>\n\n{phrase}"
        bot.answer_callback_query(c.id, "Новый совет 🌟", show_alert=False)
    else:
        return

    send_or_edit(c, text)
    logging.info(f"Пользователь {c.message.chat.id} получил совет: {phrase}")

# -------------------------
# Flask эндпоинты
# -------------------------
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.data.decode("utf-8"))
    bot.process_new_updates([update])
    return "ok", 200

@app.route("/", methods=["GET"])
def index():
    return "Бот работает!", 200

# -------------------------
# Запуск приложения
# -------------------------
if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=f"{APP_URL}/{TOKEN}")
    logging.info(f"✅ Webhook установлен: {APP_URL}/{TOKEN}")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
