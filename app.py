import os
import time
import logging
import threading
import requests
from flask import Flask, request

# --- Конфигурация ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CHECK_INTERVAL = 60 * 60  # 60 минут

NEWS_URL = "https://visa.vfsglobal.com/blr/ru/pol/news/release-appointment"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

last_news_text = None  # хранит последний текст новости

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        r = requests.post(url, json=payload)
        r.raise_for_status()
        logging.info("Отправлено в Telegram")
    except Exception as e:
        logging.warning(f"Ошибка отправки в Telegram: {e}")

def fetch_news_text():
    try:
        r = requests.get(NEWS_URL, headers=HEADERS, timeout=10)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logging.warning(f"Ошибка при получении новости: {e}")
        return None

def check_news():
    global last_news_text
    logging.info("Проверяем новости на сайте...")
    text = fetch_news_text()
    if text is None:
        return

    # Простая проверка: если содержимое изменилось — считаем новой новостью
    if last_news_text != text:
        last_news_text = text
        snippet = extract_snippet(text)
        message = f"🆕 Новая новость с VFS Global:\n\n{snippet}\n\n{NEWS_URL}"
        send_telegram(message)
        logging.info("Новая новость найдена и отправлена.")
    else:
        logging.info("Новостей не обнаружено.")

def extract_snippet(html_text, length=500):
    # Просто отрезаем первые length символов без HTML тэгов
    import re
    text_only = re.sub('<[^<]+?>', '', html_text)
    snippet = text_only.strip().replace('\n', ' ')[:length]
    return snippet + ("..." if len(text_only) > length else "")

def auto_check_loop():
    def loop():
        while True:
            try:
                check_news()
            except Exception as e:
                logging.error(f"Ошибка в авто-проверке: {e}")
            time.sleep(CHECK_INTERVAL)
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()

@app.route("/")
def health():
    return "VFS News Bot работает", 200

@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    update = request.get_json(force=True)
    message = update.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        return "ok", 200

    if text == "/start":
        send_telegram("Привет! Я слежу за новостями VFS Global. Напиши /check чтобы проверить новости сейчас.")
    elif text == "/check":
        check_news()
    elif text == "/help":
        send_telegram("/start - Запуск бота\n/check - Проверить новости\n/help - Помощь")
    else:
        send_telegram("Неизвестная команда. Напиши /help.")

    return "ok", 200

if __name__ == "__main__":
    logging.info("Бот запущен, старт авто-проверки...")
    auto_check_loop()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))