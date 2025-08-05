import os
import logging
import cloudscraper
from bs4 import BeautifulSoup
from flask import Flask, request
import threading
import time
import datetime
import telegram

# Настройки
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CHECK_INTERVAL = 3600  # каждый час

# Инициализация
bot = telegram.Bot(token=TELEGRAM_TOKEN)
app = Flask(__name__)
scraper = cloudscraper.create_scraper()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Хранение последней новости
last_news = {"title": "", "url": ""}


def check_news():
    global last_news
    while True:
        try:
            url = "https://visa.vfsglobal.com/blr/ru/pol/news/release-appointment"
            headers = {"User-Agent": "Mozilla/5.0"}
            response = scraper.get(url, headers=headers)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            news_block = soup.find("div", class_="card-body p-0 news-article")
            if news_block:
                title = news_block.find("h5").text.strip()
                link = "https://visa.vfsglobal.com" + news_block.find("a")["href"]
                if title != last_news["title"]:
                    last_news = {"title": title, "url": link}
                    message = f"🆕 Новая новость!\n\n<b>{title}</b>\n{link}"
                    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode="HTML")
                    logging.info("Найдена новая новость и отправлена в Telegram")
                else:
                    logging.info("Новых новостей нет")
            else:
                logging.warning("Блок с новостью не найден")

        except Exception as e:
            logging.error(f"[Ошибка] Не удалось получить новость: {e}")
        time.sleep(CHECK_INTERVAL)


@app.route("/")
def home():
    return f"<h2>VFS News Bot</h2><p>Последняя новость: <b>{last_news['title']}</b><br><a href='{last_news['url']}'>{last_news['url']}</a></p>"


@app.route("/check", methods=["GET"])
def manual_check():
    threading.Thread(target=check_news_once).start()
    return "Проверка запущена вручную."


@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    update = telegram.Update.de_json(request.get_json(force=True), bot)
    chat_id = update.message.chat.id
    text = update.message.text.lower()

    if "проверь" in text or "check" in text:
        bot.send_message(chat_id=chat_id, text="🔄 Проверяю новости...")
        threading.Thread(target=check_news_once).start()
    elif "статус" in text or "status" in text:
        bot.send_message(chat_id=chat_id, text=f"📝 Последняя новость:\n<b>{last_news['title']}</b>\n{last_news['url']}", parse_mode="HTML")
    else:
        bot.send_message(chat_id=chat_id, text="Команды:\n• статус\n• проверь")

    return "OK"


def check_news_once():
    try:
        url = "https://visa.vfsglobal.com/blr/ru/pol/news/release-appointment"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = scraper.get(url, headers=headers)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        news_block = soup.find("div", class_="card-body p-0 news-article")
        if news_block:
            title = news_block.find("h5").text.strip()
            link = "https://visa.vfsglobal.com" + news_block.find("a")["href"]
            if title != last_news["title"]:
                last_news.update({"title": title, "url": link})
                bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"🆕 Новая новость!\n\n<b>{title}</b>\n{link}", parse_mode="HTML")
            else:
                bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="Новостей пока нет.")
        else:
            bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="❗ Блок с новостями не найден.")
    except Exception as e:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"Ошибка: {e}")


if __name__ == "__main__":
    threading.Thread(target=check_news).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))