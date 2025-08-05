import os
import logging
import requests
from flask import Flask, request
from threading import Thread
from datetime import datetime
from time import sleep

import telegram
from telegram.ext import CommandHandler, Updater

# Логирование
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Переменные окружения
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 600))  # интервал в секундах

# Проверяем наличие токена
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    raise Exception("TELEGRAM_TOKEN и TELEGRAM_CHAT_ID должны быть заданы в переменных окружения")

# Telegram бот
bot = telegram.Bot(token=TELEGRAM_TOKEN)

# Flask-приложение
app = Flask(__name__)

# Переменные состояния
last_news_title = ""
last_check_time = None
status_message = "Бот ещё не запускался."

# URL с новостями
NEWS_URL = "https://visa.vfsglobal.com/blr/ru/pol/news/release-appointment"

def check_news():
    global last_news_title, last_check_time, status_message

    try:
        response = requests.get(NEWS_URL, timeout=10)
        response.raise_for_status()

        # Простая проверка на наличие текста о записи
        if "запись на подачу документов" in response.text.lower():
            news_found = "‼️ Обнаружена новая новость о записи!"
        else:
            news_found = "ℹ️ Пока нет новых новостей."

        last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status_message = f"{news_found}\nПроверено: {last_check_time}"
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=status_message)

        logger.info(status_message)

    except Exception as e:
        error_message = f"[Ошибка] Не удалось получить новость: {e}"
        logger.warning(error_message)
        status_message = error_message

def start_polling():
    while True:
        check_news()
        sleep(CHECK_INTERVAL)

@app.route("/", methods=["GET"])
def index():
    return f"✅ VFS News Bot работает. Последняя проверка: {last_check_time or 'ещё не было'}"

@app.route("/check", methods=["POST", "GET"])
def manual_check():
    Thread(target=check_news).start()
    return "🟡 Запущена ручная проверка."

# Телеграм команды
def start(update, context):
    update.message.reply_text("✅ Бот активен. Используйте /check для ручной проверки.")

def check_command(update, context):
    update.message.reply_text("🔄 Проверка началась...")
    Thread(target=check_news).start()

def status(update, context):
    update.message.reply_text(f"ℹ️ {status_message}")

def telegram_bot():
    updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("check", check_command))
    dp.add_handler(CommandHandler("status", status))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    # Запускаем Telegram-бота в отдельном потоке
    Thread(target=telegram_bot).start()
    # Запускаем автоматическую проверку новостей
    Thread(target=start_polling).start()

    # Запуск Flask-приложения
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))