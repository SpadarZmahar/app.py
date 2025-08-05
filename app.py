import os
import logging
import requests
from flask import Flask, request

# Настройка логов
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Переменные окружения
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ваш_токен_бота")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "ваш_чат_id")
PORT = int(os.getenv("PORT", 8080))

# Flask-приложение
app = Flask(__name__)

# Храним последнюю новость
last_news = ""

# Проверка новостей на VFS Global
def check_news():
    global last_news
    try:
        url = "https://visa.vfsglobal.com/blr/ru/pol/news/release-appointment"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        if "Внимание!" in response.text or "назначения" in response.text:
            message = "🛂 Найдена новая новость на VFS Global!"
            if message != last_news:
                send_telegram(message)
                last_news = message
                logging.info("[Найдено] %s", message)
            return message
        else:
            return "Нет новых новостей."
    except Exception as e:
        error_msg = f"[Ошибка] Не удалось получить новость: {e}"
        logging.warning(error_msg)
        return error_msg

# Отправка в Telegram
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text})
        if resp.status_code != 200:
            logging.warning("Ошибка Telegram: %s", resp.text)
    except Exception as e:
        logging.warning("Ошибка при отправке в Telegram: %s", e)

# Webhook-обработчик
@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    if request.method == "POST":
        data = request.json
        if "message" in data:
            chat_id = data["message"]["chat"]["id"]
            text = data["message"].get("text", "")
            if text == "/start":
                send_telegram("✅ Бот запущен.")
            elif text == "/check":
                result = check_news()
                send_telegram(result)
            elif text == "/status":
                send_telegram(last_news or "Новостей пока не было.")
            elif text == "/help":
                help_text = "Команды:\n/start — запуск\n/check — проверка\n/status — статус\n/help — помощь"
                send_telegram(help_text)
            else:
                send_telegram("Неизвестная команда. Напиши /help.")
    return "ok"

# Health-check
@app.route("/")
def index():
    return "VFS Telegram Bot работает."

# Запуск сервера
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)