import os
import random
import telebot

bot = telebot.TeleBot(os.getenv("BOT_TOKEN"))

# Загружаем советы из файла или дефолтные
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

# Render ждёт app
app = bot

if __name__ == "__main__":
    bot.infinity_polling(skip_pending=True)
