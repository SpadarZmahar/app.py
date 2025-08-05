import os
import logging
import time
from threading import Thread
import cloudscraper
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, CallbackContext
from bs4 import BeautifulSoup

# --- НАСТРОЙКИ ---

# Настройка логирования для вывода информативных сообщений о работе бота
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - [%(levelname)s] - %(message)s",
)
# Приглушение слишком "громких" логов от сторонних библиотек
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("cloudscraper").setLevel(logging.WARNING)


# Получение переменных окружения. Убедитесь, что они заданы в вашей среде.
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# URL страницы с новостями VFS Global
NEWS_URL = "https://visa.vfsglobal.com/blr/ru/pol/news/release-appointment"
# Интервал проверки новостей в фоновом режиме (в секундах)
CHECK_INTERVAL_SECONDS = 60 * 5  # 5 минут

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ И ИНИЦИАЛИЗАЦИЯ ---

# Flask приложение для приема вебхуков от Telegram
app = Flask(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=TELEGRAM_TOKEN)
dispatcher = Dispatcher(bot, None, workers=0, use_context=True)

# Переменная для хранения хэша последней новости
last_news_hash = None

# **ИСПРАВЛЕНИЕ 2.0**: Создаем единый сеанс (сессию) cloudscraper,
# который будет использоваться для всех запросов. Это позволяет сохранять
# cookies и результаты прохождения проверок Cloudflare между запросами.
scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)

# --- ОСНОВНЫЕ ФУНКЦИИ ---

def fetch_news():
    """
    Получает содержимое новостного блока с сайта VFS, используя единую сессию.
    """
    try:
        # **ИСПРАВЛЕНИЕ 2.0**: Добавляем более полный набор заголовков
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Cache-Control': 'max-age=0',
            'DNT': '1',
            'Upgrade-Insecure-Requests': '1'
        }
        # Используем глобальный экземпляр `scraper` и увеличиваем таймаут
        response = scraper.get(NEWS_URL, headers=headers, timeout=45)
        response.raise_for_status()  # Проверка на ошибки HTTP (4xx или 5xx)

        soup = BeautifulSoup(response.text, "html.parser")
        news_block = soup.find("div", class_="vfsg-news-content")

        if news_block:
            return news_block.text.strip()
        else:
            logging.warning("Новостной блок 'vfsg-news-content' не найден на странице.")
            return None

    except Exception as e:
        logging.error(f"Критическая ошибка при получении новости: {e}")
        return None

def send_telegram_message(message):
    """Отправляет сообщение в заданный Telegram чат."""
    if not TELEGRAM_CHAT_ID:
        logging.error("Переменная окружения TELEGRAM_CHAT_ID не установлена!")
        return
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logging.info(f"Сообщение успешно отправлено в чат {TELEGRAM_CHAT_ID}.")
    except Exception as e:
        logging.error(f"Ошибка отправки сообщения в Telegram: {e}")

def check_news_and_notify():
    """
    Проверяет наличие новой новости и отправляет уведомление.
    Возвращает статус проверки для использования в ручном режиме.
    """
    global last_news_hash
    logging.info("Начинаю проверку новостей...")

    news_text = fetch_news()

    if news_text:
        current_hash = hash(news_text)
        if last_news_hash is None:
            last_news_hash = current_hash
            logging.info("Первоначальная новость успешно загружена. Хэш сохранен.")
            send_telegram_message(f"✅ Бот успешно запущен и следит за обновлениями.\n\nТекущая новость на VFS:\n\n{news_text}")
            return "✅ Бот запущен, первая новость загружена."

        if current_hash != last_news_hash:
            logging.info("!!! НАЙДЕНО ОБНОВЛЕНИЕ !!!")
            last_news_hash = current_hash
            message = f"🆕 Обновление на VFS: \n\n{news_text}"
            send_telegram_message(message)
            return "✅ Найдено и отправлено новое обновление!"
        else:
            logging.info("Новых новостей нет.")
            return "ℹ️ Новых новостей нет. Сайт доступен."
    else:
        logging.warning("Не удалось получить текст новости для проверки.")
        return "❌ Не удалось получить новости. Проверьте логи для деталей."

# --- КОМАНДЫ TELEGRAM ---

def start_command(update: Update, context: CallbackContext):
    update.message.reply_text("✅ Привет! Я бот для отслеживания новостей VFS. Я уже запущен и проверяю обновления в фоне.")

def status_command(update: Update, context: CallbackContext):
    status_text = "🟢 Бот работает."
    if last_news_hash is None:
        status_text += "\n\n⚠️ Пока не удалось получить данные с сайта. Проверка продолжается."
    else:
        status_text += "\n\n✅ Последние данные с сайта были успешно получены."
    update.message.reply_text(status_text)

def check_command(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id
    context.bot.send_message(chat_id, "🔄 Выполняю ручную проверку новостей...")
    status_message = check_news_and_notify()
    context.bot.send_message(chat_id, status_message)

# Регистрация обработчиков команд
dispatcher.add_handler(CommandHandler("start", start_command))
dispatcher.add_handler(CommandHandler("status", status_command))
dispatcher.add_handler(CommandHandler("check", check_command))

# --- ВЕБ-СЕРВЕР И ФОНОВЫЕ ЗАДАЧИ ---

@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK"

@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

def background_news_checker():
    logging.info(f"Фоновая проверка новостей запущена. Интервал: {CHECK_INTERVAL_SECONDS} секунд.")
    time.sleep(15) # Даем серверу время на старт перед первой проверкой
    while True:
        try:
            check_news_and_notify()
            time.sleep(CHECK_INTERVAL_SECONDS)
        except Exception as e:
            logging.error(f"Сбой в цикле фоновой проверки: {e}")
            time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise ValueError("Ошибка: Не заданы обязательные переменные окружения TELEGRAM_TOKEN и TELEGRAM_CHAT_ID")

    background_thread = Thread(target=background_news_checker, daemon=True)
    background_thread.start()

    logging.info("Запуск веб-сервера Flask...")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))