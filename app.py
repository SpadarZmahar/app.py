import os
import time
import json
import logging
import threading
import requests
from flask import Flask, request

# Конфигурация
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret")
CHECK_INTERVAL = 60 * 60  # 60 минут

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

VFSGLOBAL_NEWS_URL = "https://visa.vfsglobal.com/blr/ru/pol/news/release-appointment"
VFS_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

last_news = ""

def send_telegram_message(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logging.info("[Telegram] Сообщение отправлено")
    except Exception as e:
        logging.error(f"[Telegram] Ошибка отправки: {e}")

def fetch_news():
    headers = {"User-Agent": VFS_USER_AGENT}
    try:
        response = requests.get(VFSGLOBAL_NEWS_URL, headers=headers, timeout=10)
        response.raise_for_status()
        if "запись на подачу документов" in response.text.lower():
            return "🆕 Обнаружена новая новость: запись открыта!"
        else:
            return None
    except Exception as e:
        logging.error(f"[Ошибка] Не удалось получить новость: {e}")
        return None

def check_and_notify():
    global last_news
    logging.info("[Бот] Проверка новостей...")
    news = fetch_news()
    if news and news != last_news:
        send_telegram_message(news)
        last_news = news
    else:
        logging.info("[Бот] Нет новых новостей")
    # Запланировать следующую проверку
    threading.Timer(CHECK_INTERVAL, check_and_notify).start()

@app.route("/")
def health():
    return "VFS Bot работает", 200

@app.route(f"/{WEBHOOK_SECRET}", methods=["POST"])
def telegram_webhook():
    data = request.json
    logging.info(f"[Webhook] Получено сообщение: {data}")
    if "message" in data and "text" in data["message"]:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"]["text"]
        if str(chat_id) != TELEGRAM_CHAT_ID:
            logging.warning("[Webhook] Сообщение от неизвестного chat_id")
            return "OK", 200
        if text == "/check":
            news = fetch_news()
            if news:
                send_telegram_message(news)
            else:
                send_telegram_message("Нет новых новостей.")
    return "OK", 200

if __name__ == "__main__":
    logging.info("[Бот] Запуск...")
    check_and_notify()  # Старт автопроверки
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))