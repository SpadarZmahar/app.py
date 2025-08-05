import os
import time
import threading
import requests
from flask import Flask, jsonify, request
from bs4 import BeautifulSoup
from datetime import datetime

# === Настройки ===
NEWS_URL = "https://visa.vfsglobal.com/blr/ru/pol/news/release-appointment"
STATE_FILE = "last_news.txt"
CHECK_INTERVAL = 3600  # 1 час
app = Flask(__name__)

# === Telegram ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# === Состояние бота ===
monitoring_enabled = True
last_news = ""

def read_saved_news():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

def save_news(news_text):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(news_text)

def fetch_latest_news():
    try:
        resp = requests.get(NEWS_URL, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        article = soup.select_one("div.card-text")
        return article.get_text(strip=True) if article else None
    except Exception as e:
        print(f"[Ошибка] Не удалось получить новость: {e}")
        return None

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Ошибка] Не заданы TELEGRAM_TOKEN или TELEGRAM_CHAT_ID")
        return
    requests.post(f"{TELEGRAM_API}/sendMessage", data={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    })

def monitor_loop():
    global last_news
    while True:
        if monitoring_enabled:
            current = fetch_latest_news()
            if current and current != last_news:
                last_news = current
                save_news(current)
                msg = f"🆕 Новая новость на VFS:\n\n{current}\n\n{NEWS_URL}"
                print("[Бот] Отправка новости в Telegram")
                send_telegram_message(msg)
            else:
                print("[Бот] Нет новых новостей")
        time.sleep(CHECK_INTERVAL)

@app.route("/")
def index():
    return jsonify({
        "status": "running",
        "monitoring": monitoring_enabled,
        "last_checked": datetime.utcnow().isoformat(),
        "last_news": last_news
    })

@app.route("/health")
def health():
    return "OK", 200

@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    global monitoring_enabled
    data = request.get_json()
    message = data.get("message", {})
    text = message.get("text", "")
    chat_id = str(message.get("chat", {}).get("id"))

    if chat_id != TELEGRAM_CHAT_ID:
        return "Unauthorized", 403

    if text == "/start":
        monitoring_enabled = True
        send_telegram_message("✅ Мониторинг включен")
    elif text == "/stop":
        monitoring_enabled = False
        send_telegram_message("⛔️ Мониторинг остановлен")
    elif text == "/check":
        latest = fetch_latest_news()
        send_telegram_message(f"📰 Последняя новость:\n\n{latest}")
    elif text == "/status":
        send_telegram_message("ℹ️ Бот работает.\nМониторинг: " +
                              ("включён" if monitoring_enabled else "выключен"))
    return "OK", 200

def start_bot():
    global last_news
    last_news = read_saved_news()
    thread = threading.Thread(target=monitor_loop, daemon=True)
    thread.start()

if __name__ == "__main__":
    start_bot()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))