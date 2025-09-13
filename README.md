Telegram bot — 30-day personal development program
===============================================

Что внутри:
- bot.py — основной скрипт бота
- requirements.txt — зависимости
- README.md — эта инструкция
- progress.json — создаётся автоматически при запуске

Быстрый запуск (локально):
1. Установи зависимости:
   pip install -r requirements.txt
2. Экспортируй токен:
   export TELEGRAM_TOKEN='твой_токен'   # Linux / macOS
   set TELEGRAM_TOKEN=твой_токен        # Windows (cmd)
3. Запусти:
   python bot.py
4. В Telegram найди своего бота и напиши /start

Деплой на Render (рекомендую):
1. Создай репозиторий на GitHub и залей файлы проекта.
2. Создай аккаунт на https://render.com и подключи GitHub.
3. New -> Web Service -> выбери репозиторий.
4. Environment: Python 3
   Build command: pip install -r requirements.txt
   Start command: python bot.py
5. В Settings -> Environment добавь переменную TELEGRAM_TOKEN = твой_токен
6. Deploy — бот будет работать 24/7 (Render может «засыпать» бесплатные сервисы, но он будет доступен и проснётся при обращении).

Примечания:
- progress.json создаётся автоматически в рабочей папке и хранит прогресс пользователей.
- Для масштабирования можно заменить локальное хранение на базу данных (Postgres).
