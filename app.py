# -*- coding: utf-8 -*-
import os
import logging
import time
import hashlib
from threading import Thread, Lock
import cloudscraper
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from PIL import Image
import io
import json

# --- НАСТРОЙКИ ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - [%(levelname)s] - %(message)s",
)
logger = logging.getLogger("VFSMonitor")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("cloudscraper").setLevel(logging.WARNING)
logging.getLogger("selenium").setLevel(logging.WARNING)

# Проверка критических переменных среды
def get_env_var(name, default=None):
    value = os.environ.get(name, default)
    if not value and default is None:
        logger.critical(f"Переменная окружения {name} не установлена!")
        raise ValueError(f"{name} не задана")
    return value

# Конфигурация
TELEGRAM_TOKEN = get_env_var("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = get_env_var("TELEGRAM_CHAT_ID")
NEWS_URL = "https://visa.vfsglobal.com/blr/ru/pol/news/release-appointment"
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
CHECK_INTERVAL_MINUTES = int(get_env_var("CHECK_INTERVAL_MINUTES", "60"))  # По умолчанию 60 минут
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
MAX_TEXT_LENGTH = 4000
MAX_SCREENSHOT_ATTEMPTS = 2
ERROR_NOTIFICATION_INTERVAL = 6 * 3600  # 6 часов между уведомлениями об ошибках

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
app = Flask(__name__)
application = Application.builder().token(TELEGRAM_TOKEN).build()
bot = application.bot
last_news_hash = None
last_error_time = 0
state_lock = Lock()

# Инициализация CloudScraper
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def init_selenium_driver():
    """Инициализирует headless Chrome для Selenium"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,720")
    options.add_argument(f"user-agent={USER_AGENT}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    # Для Railway
    options.binary_location = "/usr/bin/google-chrome-stable"

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    return driver

def fetch_page_content():
    """Получает содержимое страницы с обходом Cloudflare"""
    max_attempts = 3
    attempt = 0
    delay = 15

    while attempt < max_attempts:
        attempt += 1
        try:
            logger.info(f"Попытка {attempt}/{max_attempts} получения страницы")

            headers = {
                'User-Agent': USER_AGENT,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
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

            response = scraper.get(NEWS_URL, headers=headers, timeout=30)
            response.raise_for_status()

            if "cf-browser-verification" in response.text or "rocket-loader" in response.text:
                logger.warning(f"Обнаружена страница проверки Cloudflare. Ожидание {delay} сек...")
                time.sleep(delay)
                delay *= 2
                continue

            soup = BeautifulSoup(response.text, "html.parser")

            for element in soup(["script", "style", "meta", "link", "nav", "footer", "header"]):
                element.decompose()

            content_div = soup.find('div', class_='content')
            page_text = content_div.get_text(separator="\n", strip=True) if content_div else soup.get_text()

            page_text = "\n".join(line.strip() for line in page_text.split("\n") if line.strip())

            unwanted_phrases = [
                "cookie policy", "политика использования файлов cookie", "© copyright",
                "Loading...", "javascript", "vfsglobal", "cloudflare", "rocket-loader",
                "challenge", "verification", "ddos", "protection", "DDoS"
            ]
            for phrase in unwanted_phrases:
                page_text = page_text.replace(phrase, "")

            if len(page_text) > MAX_TEXT_LENGTH:
                page_text = page_text[:MAX_TEXT_LENGTH] + "\n\n... (текст обрезан)"

            return page_text

        except Exception as e:
            logger.error(f"Ошибка при получении страницы (попытка {attempt}): {str(e)}")
            time.sleep(delay)
            delay *= 2

    logger.warning("Не удалось получить содержимое через requests. Пробуем Selenium...")
    return fetch_with_selenium()

def fetch_with_selenium():
    """Использует Selenium для получения контента"""
    driver = None
    try:
        driver = init_selenium_driver()
        driver.get(NEWS_URL)
        time.sleep(5)

        if "cf-browser-verification" in driver.page_source:
            logger.warning("Cloudflare обнаружен в Selenium. Ожидание 15 секунд...")
            time.sleep(15)
            driver.refresh()
            time.sleep(10)

        content = driver.find_element("tag name", "body").text
        return content[:MAX_TEXT_LENGTH] + "\n\n... (текст обрезан)" if len(content) > MAX_TEXT_LENGTH else content

    except Exception as e:
        logger.error(f"Ошибка при получении страницы через Selenium: {str(e)}")
        return None

    finally:
        if driver:
            driver.quit()

def capture_screenshot():
    """Делает скриншот страницы и возвращает как bytes"""
    driver = None
    attempt = 0

    while attempt < MAX_SCREENSHOT_ATTEMPTS:
        attempt += 1
        try:
            driver = init_selenium_driver()
            driver.get(NEWS_URL)
            time.sleep(3)

            screenshot = driver.get_screenshot_as_png()

            img = Image.open(io.BytesIO(screenshot))
            img = img.convert('RGB')
            output = io.BytesIO()
            img.save(output, format='JPEG', quality=80)
            return output.getvalue()

        except Exception as e:
            logger.error(f"Ошибка при создании скриншота (попытка {attempt}): {str(e)}")
            time.sleep(5)

        finally:
            if driver:
                driver.quit()

    return None

async def send_telegram_message(message, image_bytes=None):
    """Отправляет сообщение и/или изображение в Telegram"""
    try:
        if image_bytes:
            caption = message if len(message) <= 1000 else message[:900] + "\n\n... (сообщение сокращено)"
            await bot.send_photo(
                chat_id=TELEGRAM_CHAT_ID,
                photo=image_bytes,
                caption=caption
            )
            logger.info("Скриншот отправлен в Telegram")
        else:
            if len(message) > 4000:
                parts = [message[i:i+4000] for i in range(0, len(message), 4000)]
                for part in parts:
                    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=part)
                    time.sleep(1)
            else:
                await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
            logger.info(f"Сообщение отправлено")
    except Exception as e:
        logger.error(f"Ошибка отправки в Telegram: {str(e)}")

def calculate_hash(content):
    """Вычисляет MD5 хеш контента"""
    return hashlib.md5(content.encode('utf-8')).hexdigest() if content else ""

def save_state():
    """Сохраняет состояние в файл (для сохранения при перезапусках)"""
    state = {
        'last_news_hash': last_news_hash,
        'last_error_time': last_error_time
    }
    try:
        with open('state.json', 'w') as f:
            json.dump(state, f)
    except Exception as e:
        logger.error(f"Ошибка сохранения состояния: {str(e)}")

def load_state():
    """Загружает состояние из файла"""
    global last_news_hash, last_error_time
    try:
        if os.path.exists('state.json'):
            with open('state.json', 'r') as f:
                state = json.load(f)
                last_news_hash = state.get('last_news_hash')
                last_error_time = state.get('last_error_time', 0)
                logger.info("Состояние успешно загружено")
    except Exception as e:
        logger.error(f"Ошибка загрузки состояния: {str(e)}")

# --- ОСНОВНАЯ ЛОГИКА ---
def check_news_and_notify_sync():
    """Синхронная версия для фонового потока"""
    global last_news_hash, last_error_time
    with state_lock:
        logger.info("Запущена проверка страницы")
        page_content = None

        try:
            page_content = fetch_page_content()

            if not page_content:
                logger.warning("Не удалось получить содержимое страницы")
                current_time = time.time()

                if current_time - last_error_time > ERROR_NOTIFICATION_INTERVAL:
                    last_error_time = current_time
                    screenshot = capture_screenshot()
                    import asyncio
                    asyncio.run(send_telegram_message(
                        f"⚠️ Ошибка получения страницы VFS!\n\n"
                        f"Ссылка: {NEWS_URL}\n"
                        f"Последняя проверка: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                        f"Отправляю скриншот последнего состояния страницы.",
                        screenshot
                    ))
                return "❌ Ошибка получения страницы"

            current_hash = calculate_hash(page_content)

            if last_news_hash is None:
                last_news_hash = current_hash
                screenshot = capture_screenshot()
                import asyncio
                asyncio.run(send_telegram_message(
                    f"✅ Бот запущен и начал мониторинг страницы VFS!\n\n"
                    f"Ссылка: {NEWS_URL}\n"
                    f"Интервал проверки: {CHECK_INTERVAL_MINUTES} минут\n\n"
                    f"Текущее содержимое страницы:\n\n{page_content}",
                    screenshot
                ))
                save_state()
                return "✅ Первоначальное содержимое загружено"

            if current_hash != last_news_hash:
                last_news_hash = current_hash
                screenshot = capture_screenshot()
                import asyncio
                asyncio.run(send_telegram_message(
                    f"🆕 ОБНОВЛЕНИЕ НА СТРАНИЦЕ VFS!\n\n"
                    f"Ссылка: {NEWS_URL}\n"
                    f"Время обнаружения: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"Новое содержимое:\n\n{page_content}",
                    screenshot
                ))
                save_state()
                return "✅ Обновление обнаружено и отправлено"

            return "ℹ️ Изменений нет"

        except Exception as e:
            logger.error(f"Критическая ошибка в check_news_and_notify: {str(e)}")
            return f"❌ Критическая ошибка: {str(e)}"

async def check_news_and_notify():
    """Асинхронный интерфейс - для команд Telegram"""
    return check_news_and_notify_sync()

# --- ОБРАБОТЧИКИ TELEGRAM ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"✅ Бот активен! Автоматически проверяю страницу VFS каждые {CHECK_INTERVAL_MINUTES} минут.\n"
        f"Ссылка: {NEWS_URL}\n\n"
        "Доступные команды:\n"
        "/status - текущий статус бота\n"
        "/check - запустить проверку вручную"
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = "🟢 Бот работает\n"
    status += f"Проверяемая страница: {NEWS_URL}\n"
    status += f"Интервал проверки: {CHECK_INTERVAL_MINUTES} минут\n"
    status += f"Последняя проверка: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"

    if WEBHOOK_URL:
        status += "✅ Вебхук настроен\n"
    else:
        status += "⚠️ Вебхук не настроен\n"

    if last_news_hash:
        status += "✅ Мониторинг активен"
    else:
        status += "🔄 Ожидание первой проверки"

    await update.message.reply_text(status)

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Запускаю ручную проверку страницы...")
    result = await check_news_and_notify()
    await update.message.reply_text(result)

# Регистрация обработчиков команд
application.add_handler(CommandHandler("start", start_command))
application.add_handler(CommandHandler("status", status_command))
application.add_handler(CommandHandler("check", check_command))

# --- WEB SERVER ---
@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
async def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), bot)
        await application.update_queue.put(update)
    except Exception as e:
        logger.error(f"Ошибка в вебхуке: {str(e)}")
    return "OK"

@app.route("/health", methods=["GET"])
def health_check():
    return json.dumps({
        "status": "ok",
        "last_check": time.strftime('%Y-%m-%d %H:%M:%S'),
        "interval_minutes": CHECK_INTERVAL_MINUTES
    }), 200

def setup_webhook():
    if not WEBHOOK_URL:
        logger.warning("WEBHOOK_URL не задан. Пропускаю настройку вебхука")
        return

    webhook_path = f"{WEBHOOK_URL}/webhook/{TELEGRAM_TOKEN}"
    try:
        bot.set_webhook(url=webhook_path)
        logger.info(f"Вебхук установлен: {webhook_path}")
    except Exception as e:
        logger.error(f"Ошибка настройки вебхука: {str(e)}")

def background_page_checker():
    time.sleep(15)  # Задержка для инициализации сервера
    logger.info(f"Фоновый мониторинг запущен. Интервал: {CHECK_INTERVAL_MINUTES} минут")

    load_state()

    while True:
        try:
            start_time = time.time()
            result = check_news_and_notify_sync()
            logger.info(result)

            elapsed = time.time() - start_time
            sleep_time = max(CHECK_INTERVAL_MINUTES * 60 - elapsed, 60)
            logger.info(f"Следующая проверка через {sleep_time/60:.1f} минут")
            time.sleep(sleep_time)

        except Exception as e:
            logger.error(f"Критическая ошибка в фоновом потоке: {str(e)}")
            time.sleep(300)  # Пауза 5 минут при ошибке

if __name__ == "__main__":
    setup_webhook()
    monitor_thread = Thread(target=background_page_checker, daemon=True)
    monitor_thread.start()

    port = int(get_env_var("PORT", "8000"))
    logger.info(f"Сервер запущен на порту {port}")

    if WEBHOOK_URL:
        app.run(host="0.0.0.0", port=port, use_reloader=False)
    else:
        application.run_polling()