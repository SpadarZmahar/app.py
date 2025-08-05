import os
import logging
import time
import hashlib
from threading import Thread
import cloudscraper
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, CallbackContext
from bs4 import BeautifulSoup

# --- НАСТРОЙКИ ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - [%(levelname)s] - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("cloudscraper").setLevel(logging.WARNING)

# Проверка критических переменных среды
def get_env_var(name):
    value = os.environ.get(name)
    if not value:
        logging.critical(f"Переменная окружения {name} не установлена!")
        raise ValueError(f"{name} не задана")
    return value

TELEGRAM_TOKEN = get_env_var("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = get_env_var("TELEGRAM_CHAT_ID")

# WEBHOOK_URL теперь опциональная переменная
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
if not WEBHOOK_URL:
    logging.warning("WEBHOOK_URL не задан. Вебхук не будет настроен автоматически")

# Конфигурация
NEWS_URL = "https://visa.vfsglobal.com/blr/ru/pol/news/release-appointment"
CHECK_INTERVAL_SECONDS = 60 * 60  # 1 час (60 минут)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
app = Flask(__name__)
bot = Bot(token=TELEGRAM_TOKEN)
dispatcher = Dispatcher(bot, None, workers=1, use_context=True)
last_news_hash = None

# Инициализация CloudScraper
scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)

# --- ОСНОВНЫЕ ФУНКЦИИ ---
def fetch_news():
    """Получает содержимое новостного блока с сайта VFS"""
    try:
        headers = {
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'DNT': '1',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-User': '?1',
            'Sec-Fetch-Dest': 'document'
        }
        
        response = scraper.get(NEWS_URL, headers=headers, timeout=60)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        
        # Улучшенный поиск новостного блока
        news_block = None
        
        # Попробуем разные стратегии поиска
        selectors = [
            "div.vfsg-news-content",        # Старый селектор
            "div.vfsg-content",             # Альтернативный класс
            "div.vfsweb-container",         # Новый контейнер
            "section.vfsg-news",            # Возможный тег section
            "div.news-content",             # Общий класс
            "div.announcement",             # Другой возможный класс
            "div.content-main",             # Основной контент
            "div.page-content",             # Контент страницы
            "div#main-content",             # ID основного контента
            "article.news-item"             # Тег article
        ]
        
        for selector in selectors:
            news_block = soup.select_one(selector)
            if news_block:
                logging.info(f"Найден блок с селектором: {selector}")
                break
        
        # Если не нашли по селекторам, попробуем найти по тексту
        if not news_block:
            logging.warning("Поиск по селекторам не дал результатов, пробуем поиск по тексту")
            possible_blocks = soup.find_all(["div", "section", "article"])
            for block in possible_blocks:
                text = block.text.lower()
                if "новост" in text or "объявлени" in text or "release" in text or "appointment" in text:
                    news_block = block
                    logging.info("Блок найден по текстовому содержанию")
                    break
        
        if not news_block:
            logging.error("Не удалось найти новостной блок на странице. Сохраняем HTML для отладки...")
            # Сохраняем HTML для анализа
            with open("page_dump.html", "w", encoding="utf-8") as f:
                f.write(response.text)
            return None
        
        # Очищаем и форматируем текст
        news_text = news_block.get_text(separator="\n", strip=True)
        news_text = "\n".join(line.strip() for line in news_text.split("\n") if line.strip())
        
        # Удаляем лишние элементы
        unwanted_phrases = ["cookie policy", "политика использования файлов cookie", "© copyright"]
        for phrase in unwanted_phrases:
            news_text = news_text.replace(phrase, "")
        
        return news_text.strip()

    except Exception as e:
        logging.error(f"Ошибка при получении новостей: {str(e)}")
        return None

def send_telegram_message(message):
    """Отправляет сообщение в Telegram чат"""
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logging.info(f"Сообщение отправлено: {message[:50]}...")
    except Exception as e:
        logging.error(f"Ошибка отправки в Telegram: {str(e)}")

def calculate_hash(content):
    """Вычисляет стабильный хеш контента"""
    return hashlib.md5(content.encode('utf-8')).hexdigest()

def check_news_and_notify():
    """Проверяет новости и отправляет уведомления"""
    global last_news_hash
    logging.info("Запущена проверка новостей")

    news_text = fetch_news()
    if not news_text:
        logging.warning("Не удалось получить новости")
        return "❌ Ошибка получения новостей"

    current_hash = calculate_hash(news_text)
    
    if last_news_hash is None:
        last_news_hash = current_hash
        send_telegram_message(f"✅ Бот запущен. Текущая новость:\n\n{news_text}")
        return "✅ Первоначальная новость загружена"
    
    if current_hash != last_news_hash:
        last_news_hash = current_hash
        message = f"🆕 ОБНОВЛЕНИЕ НА VFS:\n\n{news_text}"
        send_telegram_message(message)
        return "✅ Обновление обнаружено и отправлено"
    
    return "ℹ️ Изменений нет"

# --- ОБРАБОТЧИКИ КОМАНД TELEGRAM ---
def start_command(update: Update, context: CallbackContext):
    update.message.reply_text("✅ Бот активен! Автоматически проверяю новости VFS каждые 5 минут.")

def status_command(update: Update, context: CallbackContext):
    status = "🟢 Бот работает\n"
    status += f"Последняя проверка: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
    if WEBHOOK_URL:
        status += "✅ Вебхук настроен"
    else:
        status += "⚠️ Вебхук не настроен"
    update.message.reply_text(status)

def check_command(update: Update, context: CallbackContext):
    update.message.reply_text("🔄 Ручная проверка...")
    result = check_news_and_notify()
    update.message.reply_text(result)

# Регистрация обработчиков команд
dispatcher.add_handler(CommandHandler("start", start_command))
dispatcher.add_handler(CommandHandler("status", status_command))
dispatcher.add_handler(CommandHandler("check", check_command))

@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    """Endpoint для обработки обновлений Telegram"""
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK"

@app.route("/health", methods=["GET"])
def health_check():
    """Endpoint для проверки работоспособности"""
    return "OK", 200

def setup_webhook():
    """Настройка вебхука Telegram (только если WEBHOOK_URL задан)"""
    if not WEBHOOK_URL:
        logging.warning("WEBHOOK_URL не задан. Пропускаю настройку вебхука")
        return
        
    webhook_path = f"{WEBHOOK_URL}/webhook/{TELEGRAM_TOKEN}"
    try:
        bot.set_webhook(url=webhook_path)
        logging.info(f"Вебхук установлен: {webhook_path}")
    except Exception as e:
        logging.error(f"Ошибка настройки вебхука: {str(e)}")

def background_news_checker():
    """Фоновая проверка новостей"""
    time.sleep(10)  # Задержка для инициализации сервера
    logging.info(f"Фоновый мониторинг запущен. Интервал: {CHECK_INTERVAL_SECONDS} сек")
    
    while True:
        try:
            check_news_and_notify()
            time.sleep(CHECK_INTERVAL_SECONDS)
        except Exception as e:
            logging.error(f"Ошибка в фоновом задании: {str(e)}")
            time.sleep(60)

if __name__ == "__main__":
    # Настройка вебхука (если URL задан)
    setup_webhook()
    
    # Запуск фонового потока
    monitor_thread = Thread(target=background_news_checker, daemon=True)
    monitor_thread.start()
    
    # Запуск веб-сервера
    port = int(os.environ.get("PORT", 8080))
    logging.info(f"Сервер запущен на порту {port}")
    app.run(host="0.0.0.0", port=port)