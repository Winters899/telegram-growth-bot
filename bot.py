import os
import random
import telebot
from flask import Flask, request

TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Загружаем советы
if os.path.exists("advices.txt"):
    with open("advices.txt", encoding="utf-8") as f:
        advices = [line.strip() for line in f if line.strip()]
else:
    advices = [
        "Пей больше воды",
        "Выходи гулять каждый день",
        "Высыпайся — сон лечит всё",
        "Веди дневник благодарности",
        "Учись чему-то новому каждый день",
        "Делай маленькие шаги к большой цели",
        "Меньше соцсетей — больше реальной жизни",
        "Занимайся спортом хотя бы 10 минут в день",
        "Медитируй и отдыхай от стресса",
        "Помогай другим — добро возвращается",
    ]

# Смайлы
emojis = ["🌟", "✨", "🔥", "💡", "🌈", "💖", "🌞", "🍀", "⚡", "🌊"]

# Хэндлеры
@bot.message_handler(commands=["start"])
def start(msg):
    bot.reply_to(msg, "Привет! Я бот-советчик 🧙‍♂️\nНапиши /advice, и я дам совет!")

@bot.message_handler(commands=["advice"])
def advice(msg):
    if random.randint(1, 5) == 1:  # шанс 1 из 5 — только смайл
        text = random.choice(emojis)
    else:
        text = f"{random.choice(advices)} {random.choice(emojis)}"
    bot.reply_to(msg, text)

# Flask endpoint для Telegram
@app.route("/webhook", methods=["POST"])
def webhook():
    json_str = request.get_data().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "ok", 200

# Healthcheck (Render любит, когда есть корень)
@app.route("/", methods=["GET"])
def index():
    return "Бот работает!", 200


if __name__ == "__main__":
    # URL Render-а (например, https://your-app.onrender.com/webhook)
    WEBHOOK_URL = f"{os.getenv('RENDER_EXTERNAL_URL')}/webhook"

    # Сначала убираем старый webhook
    bot.remove_webhook()
    # Устанавливаем новый
    bot.set_webhook(url=WEBHOOK_URL)

    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
