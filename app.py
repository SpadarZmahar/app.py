import os
import logging
import time
import hashlib
import json
import requests
from threading import Thread, Lock
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackContext
from bs4 import BeautifulSoup
from PIL import Image
import io

# --- НАСТРОЙКИ ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - [%(levelname)s] - %(message)s",
)
logger = logging.getLogger("VFSMonitor")
logging.getLogger("httpx").setLevel(logging.WARNING)

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
SCRAPINGBEE_API_KEY = get_env_var("SCRAPINGBEE_API_KEY")
NEWS_URL = "https://visa.vfsglobal.com/blr/ru/pol/news/release-appointment"
WEBHOOK_URL = get_env_var("WEBHOOK_URL", "")
CHECK_INTERVAL_MINUTES = int(get_env_var("CHECK_INTERVAL_MINUTES", "60"))
MAX_TEXT_LENGTH = 4000
ERROR_NOTIFICATION_INTERVAL = 6 * 3600  # 6 часов между уведомлениями об ошибках

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
app = Flask(__name__)
last_news_hash = None
last_error_time = 0
state_lock = Lock()

# Создаем Application для Telegram
telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def fetch_page_content():
    """Получает содержимое страницы через ScrapingBee"""
    params = {
        'api_key': SCRAPINGBEE_API_KEY,
        'url': NEWS_URL,
        'render_js': 'true',
        'wait': 5000,
        'wait_for': '.content',
        'premium_proxy': 'true',
        'country_code': 'us',
        'transparent_status_code': 'true'
    }
    
    try:
        response = requests.get(
            'https://app.scrapingbee.com/api/v1/',
            params=params,
            timeout=60
        )
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Удаляем ненужные элементы
            for element in soup(["script", "style", "meta", "link", "nav", "footer", "header"]):
                element.decompose()
            
            # Извлекаем основной контент
            content_div = soup.find('div', class_='content') or soup.find('main') or soup
            page_text = content_div.get_text(separator="\n", strip=True)
            
            # Очистка текста
            page_text = "\n".join(line.strip() for line in page_text.split("\n") if line.strip())
            
            # Удаляем технические фразы
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
        else:
            logger.error(f"Ошибка ScrapingBee: {response.status_code} - {response.text[:200]}")
            return None
            
    except Exception as e:
        logger.error(f"Ошибка при получении страницы: {str(e)}")
        return None

def capture_screenshot():
    """Делает скриншот страницы через ScrapingBee (требует Pro-аккаунт)"""
    params = {
        'api_key': SCRAPINGBEE_API_KEY,
        'url': NEWS_URL,
        'screenshot': 'true',
        'screenshot_full_page': 'true',
        'wait': 3000,
        'window_width': 1200,
        'premium_proxy': 'true',
        'block_ads': 'true'
    }
    
    try:
        response = requests.get(
            'https://app.scrapingbee.com/api/v1/',
            params=params,
            timeout=60
        )
        
        if response.status_code == 200:
            img = Image.open(io.BytesIO(response.content))
            img = img.convert('RGB')
            output = io.BytesIO()
            img.save(output, format='JPEG', quality=80)
            return output.getvalue()
        else:
            logger.error(f"Ошибка получения скриншота: {response.status_code}")
            return None
            
    except Exception as e:
        logger.error(f"Ошибка при создании скриншота: {str(e)}")
        return None

async def send_telegram_message(message, image_bytes=None):
    """Отправляет сообщение и/или изображение в Telegram"""
    try:
        if image_bytes:
            caption = message if len(message) <= 1000 else message[:900] + "\n\n... (сообщение сокращено)"
            await telegram_app.bot.send_photo(
                chat_id=TELEGRAM_CHAT_ID,
                photo=image_bytes,
                caption=caption
            )
            logger.info("Скриншот отправлен в Telegram")
        else:
            if len(message) > 4000:
                parts = [message[i:i+4000] for i in range(0, len(message), 4000)]
                for part in parts:
                    await telegram_app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=part)
                    time.sleep(1)
            else:
                await telegram_app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
            logger.info(f"Сообщение отправлено")
    except Exception as e:
        logger.error(f"Ошибка отправки в Telegram: {str(e)}")

def calculate_hash(content):
    """Вычисляет MD5 хеш контента"""
    return hashlib.md5(content.encode('utf-8')).hexdigest() if content else ""

def save_state():
    """Сохраняет состояние в файл"""
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
        logger.error(f"Ошибка загрузения состояния: {str(e)}")

# --- ОСНОВНАЯ ЛОГИКА ---
async def check_news_and_notify():
    """Проверяет страницу и отправляет уведомления при изменениях"""
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
                    await send_telegram_message(
                        f"⚠️ Ошибка получения страницы VFS!\n\n"
                        f"Ссылка: {NEWS_URL}\n"
                        f"Последняя проверка: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                return "❌ Ошибка получения страницы"
            
            current_hash = calculate_hash(page_content)
            
            # Первый запуск
            if last_news_hash is None:
                last_news_hash = current_hash
                await send_telegram_message(
                    f"✅ Бот запущен и начал мониторинг страницы VFS!\n\n"
                    f"Ссылка: {NEWS_URL}\n"
                    f"Интервал проверки: {CHECK_INTERVAL_MINUTES} минут\n\n"
                    f"Текущее содержимое страницы:\n\n{page_content}"
                )
                save_state()
                return "✅ Первоначальное содержимое загружено"
            
            # Обнаружены изменения
            if current_hash != last_news_hash:
                last_news_hash = current_hash
                await send_telegram_message(
                    f"🆕 ОБНОВЛЕНИЕ НА СТРАНИЦЕ VFS!\n\n"
                    f"Ссылка: {NEWS_URL}\n"
                    f"Время обнаружения: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"Новое содержимое:\n\n{page_content}"
                )
                save_state()
                return "✅ Обновление обнаружено и отправлено"
            
            return "ℹ️ Изменений нет"
        
        except Exception as e:
            logger.error(f"Критическая ошибка в check_news_and_notify: {str(e)}")
            return f"❌ Критическая ошибка: {str(e)}"

# --- ОБРАБОТЧИКИ TELEGRAM ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    await update.message.reply_text(
        f"✅ Бот активен! Автоматически проверяю страницу VFS каждые {CHECK_INTERVAL_MINUTES} минут.\n"
        f"Ссылка: {NEWS_URL}\n\n"
        "Доступные команды:\n"
        "/status - текущий статус бота\n"
        "/check - запустить проверку вручную"
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /status"""
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

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /check"""
    await update.message.reply_text("🔄 Запускаю ручную проверку страницы...")
    result = await check_news_and_notify()
    await update.message.reply_text(result)

# Регистрация обработчиков команд
telegram_app.add_handler(CommandHandler("start", start_command))
telegram_app.add_handler(CommandHandler("status", status_command))
telegram_app.add_handler(CommandHandler("check", check_command))

# --- WEB SERVER ---
@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
async def webhook():
    """Endpoint для обработки обновлений Telegram"""
    await telegram_app.update_queue.put(
        Update.de_json(data=request.get_json(), bot=telegram_app.bot)
    )
    return {"status": "ok"}, 200

@app.route("/health", methods=["GET"])
def health_check():
    """Endpoint для проверки работоспособности"""
    return json.dumps({
        "status": "ok",
        "last_check": time.strftime('%Y-%m-%d %H:%M:%S'),
        "interval_minutes": CHECK_INTERVAL_MINUTES
    }), 200

async def setup_webhook():
    """Настройка вебхука Telegram"""
    if not WEBHOOK_URL:
        logger.warning("WEBHOOK_URL не задан. Пропускаю настройку вебхука")
        return
        
    webhook_path = f"{WEBHOOK_URL}/webhook/{TELEGRAM_TOKEN}"
    try:
        await telegram_app.bot.set_webhook(url=webhook_path)
        logger.info(f"Вебхук установлен: {webhook_path}")
    except Exception as e:
        logger.error(f"Ошибка настройки вебхука: {str(e)}")

def background_page_checker():
    """Фоновая проверка страницы"""
    time.sleep(15)  # Задержка для инициализации сервера
    logger.info(f"Фоновый мониторинг запущен. Интервал: {CHECK_INTERVAL_MINUTES} минут")
    
    # Загрузка сохраненного состояния
    load_state()
    
    # Основной цикл проверки
    while True:
        try:
            start_time = time.time()
            # Запуск асинхронной функции
            telegram_app.create_task(check_news_and_notify())
            
            # Расчет времени до следующей проверки
            elapsed = time.time() - start_time
            sleep_time = max(CHECK_INTERVAL_MINUTES * 60 - elapsed, 60)
            logger.info(f"Следующая проверка через {sleep_time/60:.1f} минут")
            time.sleep(sleep_time)
            
        except Exception as e:
            logger.error(f"Критическая ошибка в фоновом потоке: {str(e)}")
            time.sleep(300)  # Пауза 5 минут при ошибке

async def start_bot():
    """Запуск Telegram бота"""
    # Настройка вебхука
    await setup_webhook()
    
    # Запуск фонового потока
    thread = Thread(target=background_page_checker, daemon=True)
    thread.start()
    
    # Запуск обработки обновлений
    await telegram_app.initialize()
    await telegram_app.start()
    logger.info("Telegram бот запущен")

if __name__ == "__main__":
    # Запуск веб-сервера
    port = int(get_env_var("PORT", "8000"))
    
    # Запуск бота в асинхронном режиме
    import asyncio
    loop = asyncio.get_event_loop()
    
    # Создаем и запускаем задачи
    loop.create_task(start_bot())
    app.run(host="0.0.0.0", port=port, use_reloader=False)