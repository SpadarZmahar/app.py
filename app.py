import os
import logging
import json
import cloudscraper
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler
from threading import Thread
from bs4 import BeautifulSoup

# Настройки логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Переменные окружения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

NEWS_URL = "https://visa.vfsglobal.com/blr/ru/pol/news/release-appointment"
app = Flask(__name__)
bot = Bot(token=TELEGRAM_TOKEN)
dispatcher = Dispatcher(bot, None, workers=0)

last_news_hash = None

def fetch_news():
    try:
        scraper = cloudscraper.create_scraper()
        response = scraper.get(NEWS_URL)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        news_block = soup.find("div", class_="vfsg-news-content")
        return news_block.text.strip() if news_block else None
    except Exception as e:
        logging.warning(f"Ошибка при получении новости: {e}")
        return None

def check_news():
    global last_news_hash
    logging.info("Проверка новостей...")
    news_text = fetch_news()
    if news_text:
        current_hash = hash(news_text)
        if current_hash != last_news_hash:
            last_news_hash = current_hash
            send_telegram_message(f"🆕 Обновление на VFS: \n\n{news_text}")
        else:
            logging.info("Новостей нет")
    else:
        logging.info("Не удалось получить новости.")

def send_telegram_message(message):
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logging.info("Отправлено сообщение в Telegram")
    except Exception as e:
        logging.error(f"Ошибка отправки в Telegram: {e}")

# Команды Telegram
def start(update: Update, context):
    update.message.reply_text("✅ Бот запущен и следит за новостями.")

def status(update: Update, context):
    update.message.reply_text("🟢 Бот работает.")

def check(update: Update, context):
    update.message.reply_text("🔄 Ручная проверка...")
    check_news()

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("status", status))
dispatcher.add_handler(CommandHandler("check", check))

# Webhook
@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK"

# Health-check
@app.route("/health", methods=["GET"])
def health():
    return "OK", 200

# Запуск автоматической проверки в отдельном потоке
def background_news_check():
    import time
    while True:
        check_news()
        time.sleep(60 * 5)  # каждые 5 минут

if __name__ == "__main__":
    Thread(target=background_news_check, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))