import os
import logging
from waitress import serve
from bot import app, bot, TOKEN, APP_URL

if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=f"{APP_URL}/{TOKEN}")
    logging.info(f"✅ Webhook установлен: {APP_URL}/{TOKEN}")
    port = int(os.environ.get("PORT", 5000))
    serve(app, host="0.0.0.0", port=port)
