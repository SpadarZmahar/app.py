import os
import time
import logging
import threading
import cloudscraper
from flask import Flask, request
import telegram

# --- Конфигурация ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))  # в минутах

NEWS_URL = "https://visa.vfsglobal.com/blr/ru/pol/news/release-appointment"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
bot = telegram.Bot(token=TELEGRAM_TOKEN)
scraper = cloudscraper.create_scraper()

last_news_text = None

def send_telegram(message):
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode="Markdown")
        logger.info("Отправлено сообщение в Telegram")
    except Exception as e:
        logger.error(f"Ошибка отправки в Telegram: {e}")

def fetch_news_text():
    try:
        r = scraper.get(NEWS_URL, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.warning(f"Ошибка при получении новости: {e}")
        return None

def extract_snippet(html_text, length=500):
    import re
    text_only = re.sub('<[^<]+?>', '', html_text)
    snippet = text_only.strip().replace('\n', ' ')[:length]
    return snippet + ("..." if len(text_only) > length else "")

def check_news(manual=False):
    global last_news_text
    logger.info("Проверка новостей...")
    text = fetch_news_text()
    if text is None:
        return
    if last_news_text != text:
        last_news_text = text
        snippet = extract_snippet(text)
        msg = f"🆕 *Новая новость VFS Global:*\n\n{snippet}\n\n[Перейти к новости]({NEWS_URL})"
        send_telegram(msg)
    else:
        if manual:
            send_telegram("Нет новых новостей.")
        logger.info("Новостей не обнаружено.")

def auto_check_loop():
    def loop():
        while True:
            try:
                check_news()
            except Exception as e:
                logger.error(f"Ошибка в авто-проверке: {e}")
            time.sleep(CHECK_INTERVAL * 60)
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()

@app.route("/")
def health():
    return "VFS News Bot работает", 200

@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    update = telegram.Update.de_json(request.get_json(force=True), bot)
    message = update.message
    if not message:
        return "ok", 200
    chat_id = message.chat.id
    text = message.text or ""

    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        logger.warning(f"Сообщение от незарегистрированного chat_id: {chat_id}")
        return "ok", 200

    text_lower = text.strip().lower()

    if text_lower == "/start":
        send_telegram("Привет! Я слежу за новостями VFS Global.\nКоманды:\n/start\n/check\n/status\n/help")
    elif text_lower == "/check":
        check_news(manual=True)
    elif text_lower == "/status":
        from datetime import datetime
        now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        send_telegram(f"📡 Бот работает.\nТекущее время: {now}")
    elif text_lower == "/help":
        send_telegram("/start - Приветствие\n/check - Проверить новости\n/status - Статус бота\n/help - Помощь")
    else:
        send_telegram("Неизвестная команда. Напиши /help.")

    return "ok", 200

if __name__ == "__main__":
    logger.info("Бот запущен, стартуем авто-проверку...")
    auto_check_loop()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))