import os
import time
import logging
import json
import threading
import cloudscraper
from datetime import datetime
from flask import Flask, request
import telegram

# === Конфигурация ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL", 60))

CITIES = {
    "91": "Минск",
    "92": "Гомель",
    "93": "Могилёв",
    "94": "Витебск"
}

# Логирование
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Телеграм бот
bot = telegram.Bot(token=TELEGRAM_TOKEN)

# Flask приложение
app = Flask(__name__)

# cloudscraper с обходом защиты
scraper = cloudscraper.create_scraper()

# === Проверка слотов ===
def check_slots():
    logger.info("Проверка доступности слотов...")
    found = False
    messages = []
    for city_id, city_name in CITIES.items():
        try:
            url = f"https://visa.vfsglobal.com/bel/ru/pol/book-appointment/api/slots/availability?city_id={city_id}&category_id=50&sub_category_id=676"
            r = scraper.get(url)
            if r.status_code == 200:
                data = r.json()
                if data.get("available"):
                    logger.info(f"✅ Слот найден: {city_name}")
                    messages.append(f"✅ Слот доступен в городе *{city_name}*")
                    found = True
                else:
                    logger.info(f"❌ Нет слота: {city_name}")
            else:
                logger.warning(f"⚠️ Ошибка при запросе {city_name}: {r.status_code}")
        except Exception as e:
            logger.error(f"⚠️ Ошибка при проверке {city_name}: {str(e)}")

    if found:
        send_telegram("\n".join(messages))
    else:
        logger.info("Слоты не найдены.")

# === Telegram уведомление ===
def send_telegram(text):
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode="Markdown")
        logger.info("📨 Уведомление отправлено в Telegram")
    except Exception as e:
        logger.error(f"Ошибка отправки в Telegram: {e}")

# === Автоматическая проверка ===
def start_loop():
    def loop():
        while True:
            try:
                check_slots()
            except Exception as e:
                logger.error(f"❌ Ошибка в цикле: {e}")
            time.sleep(CHECK_INTERVAL_MINUTES * 60)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()

# === Webhook обработчик ===
@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    update = telegram.Update.de_json(request.get_json(force=True), bot)
    chat_id = update.message.chat.id
    text = update.message.text.strip().lower()

    if text == "/start":
        bot.send_message(chat_id=chat_id, text="🤖 Бот запущен. Проверяю слоты каждый час.")
    elif text == "/check":
        bot.send_message(chat_id=chat_id, text="🔍 Ручная проверка слотов...")
        check_slots()
    elif text == "/status":
        now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        bot.send_message(chat_id=chat_id, text=f"📡 Бот работает. Текущее время: {now}")
    else:
        bot.send_message(chat_id=chat_id, text="Неизвестная команда. Используй /start, /check, /status")
    return "ok"

# === Health-check для Railway ===
@app.route("/", methods=["GET"])
def root():
    return "✅ VFS бот работает"

# === Запуск ===
if __name__ == "__main__":
    logger.info("🚀 Бот запущен.")
    start_loop()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))