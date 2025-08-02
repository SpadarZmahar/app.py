import os
import time
import logging
import requests
import json
import secrets
import re
from flask import Flask, Response, request, render_template
from datetime import datetime, timedelta
from threading import Thread, Lock, Event
from bs4 import BeautifulSoup
import pytz
import cloudscraper
import random
import sentry_sdk
import urllib.parse
from sentry_sdk.integrations.flask import FlaskIntegration
from requests.exceptions import RequestException, Timeout
import base64

# Инициализация Sentry
if os.getenv('SENTRY_DSN'):
    sentry_sdk.init(
        dsn=os.getenv('SENTRY_DSN'),
        integrations=[FlaskIntegration()],
        traces_sample_rate=1.0
    )

# Настройка логов - ВКЛЮЧЕН DEBUG РЕЖИМ!
logging.basicConfig(
    level=logging.DEBUG,  # DEBUG для диагностики
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")
ANTI_CAPTCHA_KEY = os.getenv("ANTI_CAPTCHA_KEY")
BASE_URL = os.getenv("BASE_URL", "https://visa.vfsglobal.com/blr/ru/pol")
WEBHOOK_SECRET_PATH = os.getenv("WEBHOOK_SECRET_PATH", f"/webhook/{secrets.token_urlsafe(32)}")
STATE_FILE = "vfs_state.json"
NOTIFICATION_INTERVAL = 3600
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "1800"))  # 30 минут по умолчанию
VISA_CATEGORY = "National Visa D"
REQUEST_TIMEOUT = 60  # Увеличенный таймаут
MAX_LOGIN_ATTEMPTS = 2  # Уменьшено из-за блокировок
MAX_CAPTCHA_ATTEMPTS = 3  # Увеличено для сложных капч
ACCOUNT_LOCK_DEFAULT = 300  # 5 минут при блокировке

# Минский часовой пояс
MINSK_TZ = pytz.timezone("Europe/Minsk")

# Потокобезопасные глобальные переменные
session_lock = Lock()
last_notification_lock = Lock()
account_lock = Lock()
session = None
session_time = None
last_notification = None
account_locked_until = None
SESSION_EXPIRY = timedelta(minutes=10)  # Уменьшено время жизни сессии
active_checks = {}  # Словарь для отслеживания активных проверок

# Конфигурация ВЦ с подкатегориями
VISA_CENTERS = {
    "Минск": {
        "center_id": "65",
        "subcategories": ["Wszystkie", "Kierowcy"]
    },
    "Витебск": {
        "center_id": "67",
        "subcategories": []  # Без подкатегорий
    },
    "Гомель": {
        "center_id": "66",
        "subcategories": ["Wszystkie", "Kierowcy"]
    },
    "Могилёв": {
        "center_id": "68",
        "subcategories": ["Wszystkie", "Kierowcy"]
    }
}

app = Flask(__name__)

def reset_session():
    global session, session_time
    with session_lock:
        logger.info("🔄 Сбрасываю сессию")
        session = None
        session_time = None

def get_session():
    global session, session_time
    with session_lock:
        if session is None or session_time is None or datetime.utcnow() - session_time > SESSION_EXPIRY:
            logger.info("🔑 Требуется новая сессия")
            reset_session()
            session = login_session()
            if session:
                session_time = datetime.utcnow()
                logger.info("✅ Новая сессия создана")
        return session

def send_telegram_message(chat_id: str = None, message: str = None) -> bool:
    if not TELEGRAM_TOKEN:
        logger.error("❌ Отсутствует TELEGRAM_TOKEN")
        return False
        
    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    if not target_chat_id:
        logger.error("❌ Отсутствует TELEGRAM_CHAT_ID")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": target_chat_id, "text": message, "parse_mode": "HTML"}
    
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return True
    except RequestException as e:
        logger.error(f"❌ Ошибка отправки в Telegram: {str(e)}")
        return False

def solve_captcha(base64_image: str) -> str:
    """Решает капчу через сервис Anti-Captcha.com"""
    if not ANTI_CAPTCHA_KEY:
        logger.error("❌ Отсутствует ключ Anti-Captcha")
        return None
    
    try:
        # Создаем задание на решение капчи
        create_task_url = "https://api.anti-captcha.com/createTask"
        task_payload = {
            "clientKey": ANTI_CAPTCHA_KEY,
            "task": {
                "type": "ImageToTextTask",
                "body": base64_image,
                "phrase": False,
                "case": False,
                "numeric": 0,
                "math": False,
                "minLength": 0,
                "maxLength": 0
            }
        }
        
        response = requests.post(create_task_url, json=task_payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        task_data = response.json()
        
        if task_data.get("errorId", 0) > 0:
            logger.error(f"❌ Ошибка Anti-Captcha: {task_data.get('errorDescription')}")
            return None
            
        task_id = task_data.get("taskId")
        if not task_id:
            logger.error("❌ Не удалось получить taskId от Anti-Captcha")
            return None
            
        # Проверяем решение
        get_result_url = "https://api.anti-captcha.com/getTaskResult"
        result_payload = {"clientKey": ANTI_CAPTCHA_KEY, "taskId": task_id}
        
        # Ожидаем решения (макс 60 сек)
        for _ in range(12):
            time.sleep(5)
            response = requests.post(get_result_url, json=result_payload, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            result_data = response.json()
            
            if result_data.get("status") == "ready":
                solution = result_data.get("solution", {}).get("text")
                if solution:
                    logger.info(f"✅ Капча решена: {solution}")
                    return solution
            
            if result_data.get("errorId", 0) > 0:
                logger.error(f"❌ Ошибка при получении решения капчи: {result_data.get('errorDescription')}")
                return None
                
        logger.error("⌛ Таймаут решения капчи")
        return None
        
    except Exception as e:
        logger.error(f"❌ Ошибка при решении капчи: {str(e)}")
        return None

def parse_lock_time(error_text: str) -> int:
    """Парсит время блокировки из сообщения об ошибке"""
    default = ACCOUNT_LOCK_DEFAULT
    try:
        match = re.search(r'(\d+)\s+min', error_text, re.IGNORECASE)
        if match: 
            return int(match.group(1)) * 60
    except Exception:
        pass
    return default

def login_session():
    """Выполняет вход в систему с обработкой капчи и повторными попытками"""
    global account_locked_until
    logger.debug("🔑 Начинаю вход в систему...")
    for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
        try:
            logger.debug(f"🔑 Попытка входа #{attempt}/{MAX_LOGIN_ATTEMPTS}")
            
            logger.debug("🛠️ Создаю cloudscraper сессию")
            scraper = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'desktop': True,
                    'mobile': False
                },
                delay=10,
                debug=False
            )
            
            login_url = f"{BASE_URL}/account/login"
            logger.debug(f"🌐 Загружаю страницу входа: {login_url}")
            resp = scraper.get(login_url, timeout=REQUEST_TIMEOUT)
            logger.debug(f"🚩 Статус ответа: {resp.status_code}")
            resp.raise_for_status()
            
            logger.debug("🔍 Ищу CSRF-токен на странице")
            soup = BeautifulSoup(resp.text, 'html.parser')
            csrf_token = soup.find('input', {'name': '_csrf'})['value'] if soup.find('input', {'name': '_csrf'}) else None
            logger.debug(f"🔑 CSRF-токен: {csrf_token[:10]}...")
            
            # Проверяем наличие капчи
            captcha_img = soup.select_one('img.captcha-img')
            captcha_solution = None
            
            if captcha_img and 'src' in captcha_img.attrs:
                captcha_url = captcha_img['src']
                if captcha_url.startswith('data:image'):
                    # Извлекаем base64 изображение
                    base64_data = captcha_url.split(',', 1)[1]
                    logger.warning("⚠️ Обнаружена капча, пытаюсь решить...")
                    if ANTI_CAPTCHA_KEY:
                        logger.debug("🔄 Пытаюсь решить капчу")
                        captcha_solution = solve_captcha(base64_data)
                        if captcha_solution:
                            logger.debug(f"🔢 Решение капчи: {captcha_solution}")
                        else:
                            logger.error("❌ Не удалось решить капчу")
                    else:
                        logger.error("❌ Ключ anti-captcha отсутствует!")
            
            login_data = {
                "email": EMAIL,
                "password": PASSWORD,
                "_csrf": csrf_token
            }
            
            if captcha_solution:
                login_data["captcha"] = captcha_solution
            
            logger.debug(f"📤 Отправляю данные входа (email: {EMAIL})")
            time.sleep(random.uniform(3, 7))
            response = scraper.post(login_url, data=login_data, timeout=REQUEST_TIMEOUT)
            logger.debug(f"🚩 Статус входа: {response.status_code}")
            
            if "account/dashboard" in response.url:
                logger.info("✅ Успешный вход")
                return scraper
                
            # Проверяем ошибки
            logger.warning("⚠️ Не удалось войти, анализирую ошибку...")
            error_soup = BeautifulSoup(response.text, 'html.parser')
            error_div = error_soup.select_one('div.alert-danger')
            if error_div:
                error_text = error_div.get_text(strip=True)
                logger.error(f"❌ Ошибка входа: {error_text}")
                
                # Если это капча, пробуем еще раз
                if "captcha" in error_text.lower() and attempt < MAX_LOGIN_ATTEMPTS:
                    logger.info("🔄 Пробую войти снова из-за ошибки капчи")
                    continue
                    
                # Обработка блокировки аккаунта
                if "locked" in error_text.lower():
                    lock_time = parse_lock_time(error_text)
                    logger.error(f"🔒 Обнаружена блокировка аккаунта на {lock_time} сек")
                    with account_lock:
                        account_locked_until = datetime.utcnow() + timedelta(seconds=lock_time)
                    return None
                    
            # Сохраняем HTML для диагностики
            try:
                with open("login_error.html", "w", encoding="utf-8") as f:
                    f.write(response.text)
                logger.info("💾 Сохранен HTML страницы с ошибкой: login_error.html")
            except Exception as e:
                logger.error(f"❌ Ошибка сохранения HTML: {str(e)}")
                
            logger.error("❌ Не удалось войти в аккаунт")
            
        except Exception as e:
            logger.exception(f"🔥 Критическая ошибка при входе: {str(e)}")
        
        # Задержка перед следующей попыткой
        if attempt < MAX_LOGIN_ATTEMPTS:
            delay = random.uniform(5, 15)
            logger.info(f"⏱️ Повторная попытка через {delay:.1f} сек...")
            time.sleep(delay)
    
    logger.error("❌ Все попытки входа исчерпаны")
    return None

def get_center_page(city: str, subcategory: str = ""):
    global account_locked_until
    """Получает страницу с расписанием с обработкой ошибок и капчи"""
    # Проверка блокировки аккаунта
    with account_lock:
        if account_locked_until and datetime.utcnow() < account_locked_until:
            wait_sec = (account_locked_until - datetime.utcnow()).total_seconds()
            logger.warning(f"⏳ Аккаунт заблокирован, ожидаем {wait_sec:.0f} сек")
            time.sleep(wait_sec + 5)  # Ждем блокировку + 5 секунд
            account_locked_until = None  # Сбрасываем блокировку

    logger.debug(f"🌐 Загружаю страницу для {city} ({subcategory})...")
    config = VISA_CENTERS[city]
    scraper = get_session()
    if not scraper:
        return None
    
    for attempt in range(1, MAX_CAPTCHA_ATTEMPTS + 1):
        try:
            time.sleep(random.uniform(2, 5))  # Увеличенная задержка
            url = f"{BASE_URL}/book-an-appointment"
            params = {"category": VISA_CATEGORY}
            resp = scraper.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            
            # Проверяем наличие капчи
            if "Please verify you are a human" in resp.text:
                logger.warning(f"⚠️ Обнаружена капча для {city} ({subcategory}), попытка {attempt}/{MAX_CAPTCHA_ATTEMPTS}")
                reset_session()
                scraper = get_session()  # Получаем новую сессию
                if not scraper:
                    return None
                continue
            
            # Проверяем блокировку в ответе
            if "been locked" in resp.text:
                lock_time = parse_lock_time(resp.text)
                logger.error(f"🔒 Блокировка при загрузке страницы на {lock_time} сек")
                with account_lock:
                    account_locked_until = datetime.utcnow() + timedelta(seconds=lock_time)
                return "account_locked"
            
            if config.get("center_id"):
                center_select_url = f"{BASE_URL}/application-center/select"
                center_data = {
                    "centerId": config["center_id"],
                    "category": VISA_CATEGORY,
                    "subCategory": subcategory,
                    "numberOfApplicants": "1"
                }
                
                time.sleep(random.uniform(1.0, 2.0))
                resp = scraper.post(center_select_url, data=center_data, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                
                # Снова проверяем капчу после POST
                if "Please verify you are a human" in resp.text:
                    logger.warning(f"⚠️ Обнаружена капча после выбора центра, попытка {attempt}/{MAX_CAPTCHA_ATTEMPTS}")
                    reset_session()
                    scraper = get_session()
                    if not scraper:
                        return None
                    continue
                
                # Проверяем блокировку после POST
                if "been locked" in resp.text:
                    lock_time = parse_lock_time(resp.text)
                    logger.error(f"🔒 Блокировка при выборе центра на {lock_time} сек")
                    with account_lock:
                        account_locked_until = datetime.utcnow() + timedelta(seconds=lock_time)
                    return "account_locked"
            
            logger.debug(f"✅ Страница для {city} ({subcategory}) загружена")
            return resp.text
            
        except Timeout:
            logger.error(f"⌛ Таймаут при загрузке страницы {city} ({subcategory})")
        except Exception as e:
            logger.error(f"⚠️ Ошибка загрузки страницы {city} ({subcategory}): {str(e)}")
        
        # Задержка перед повторной попыткой
        if attempt < MAX_CAPTCHA_ATTEMPTS:
            delay = random.uniform(5, 15)
            logger.info(f"⏱️ Повторная попытка через {delay:.1f} сек...")
            time.sleep(delay)
    
    logger.error(f"❌ Не удалось загрузить страницу для {city} ({subcategory})")
    return None

def get_page_status(city: str, subcategory: str = "") -> str:
    try:
        logger.debug(f"🔎 Проверка {city} ({subcategory})...")
        page_content = get_center_page(city, subcategory)
        if page_content is None:
            return "error"
        if page_content == "account_locked":  # Новый статус блокировки
            return "account_locked"
        
        if "Service is currently unavailable" in page_content:
            logger.warning(f"⚠️ Сервис недоступен для {city} ({subcategory})")
            return "service_unavailable"
        
        soup = BeautifulSoup(page_content, 'html.parser')
        
        no_slots = soup.find('div', class_='alert-danger')
        if no_slots and "нет доступных слотов" in no_slots.text:
            return "no_slots"
        
        available_slots = soup.select('.appointment-card:not(.disabled)')
        if available_slots:
            return "slots_available"
            
        return "no_slots"
    except Exception as e:
        logger.error(f"⚠️ Необработанная ошибка для {city} ({subcategory}): {str(e)}")
        return "error"

def generate_status_report(state: dict) -> str:
    status_lines = []
    for key, status in state.items():
        if '|' in key:
            city, subcat = key.split('|', 1)
            display_name = f"{city} ({subcat})" if subcat else city
        else:
            city = key
            display_name = city
            
        if status == "slots_available":
            status_lines.append(f"{display_name}: ✅ Слоты доступны")
        elif status == "no_slots":
            status_lines.append(f"{display_name}: ❌ Нет слотов")
        elif status == "account_locked":
            status_lines.append(f"{display_name}: 🔒 Аккаунт заблокирован")
        elif status in ["error", "service_unavailable", "captcha_required"]:
            status_lines.append(f"{display_name}: ⚠️ Ошибка ({status})")
        else:
            status_lines.append(f"{display_name}: ❓ Неизвестный статус")
    return "\n".join(status_lines)

def should_notify() -> bool:
    global last_notification
    with last_notification_lock:
        current_time = datetime.utcnow()
        if last_notification is None or (current_time - last_notification).total_seconds() >= NOTIFICATION_INTERVAL:
            last_notification = current_time
            return True
        return False

def is_within_schedule() -> bool:
    now = datetime.now(MINSK_TZ)
    # Понедельник (0) - Пятница (4) и время с 8:00 до 17:59 (т.е. 8 <= hour < 18)
    within = now.weekday() <= 4 and 8 <= now.hour < 18
    if not within:
        logger.info(f"⏰ Вне расписания: {now.strftime('%A %H:%M')} Минск")
    return within

def load_state() -> dict:
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                # Если формат старый (нет ключей с '|'), игнорируем его
                if state and not any('|' in key for key in state.keys()):
                    logger.info("Обнаружен старый формат файла состояния. Сбрасываю для полной проверки.")
                    return {}
                return state
    except Exception as e:
        logger.error(f"⚠️ Ошибка загрузки состояния: {str(e)}")
    return {}

def save_state(state: dict):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"⚠️ Ошибка сохранения состояния: {str(e)}")

def check_slots(chat_id: str = None) -> bool:
    start_time = time.time()
    logger.debug("🔍 Начинаю проверку слотов...")
    
    # Для ручных проверок отправляем уведомление о начале
    if chat_id:
        send_telegram_message(chat_id, "🔍 Начинаю проверку слотов...")
    
    # Ручные проверки всегда выполняются вне зависимости от расписания
    is_manual_check = chat_id is not None
    
    if not is_manual_check and not is_within_schedule():
        msg = "⏰ Пропуск проверки вне расписания"
        logger.info(msg)
        return False

    # Проверяем вход в систему
    login_start = time.time()
    logger.debug("🔐 Проверяем сессию...")
    session = get_session()
    login_time = time.time() - login_start
    
    if session:
        login_status = "✅ Успешный вход"
    else:
        login_status = "❌ Ошибка входа"
    
    login_msg = f"Статус входа: {login_status}\nВремя входа: {login_time:.2f} сек"
    logger.info(login_msg)
    
    if chat_id:
        send_telegram_message(chat_id, login_msg)
    
    # Если вход не удался - выходим
    if not session:
        error_msg = "❌ Не удалось войти в систему. Проверка невозможна."
        logger.error(error_msg)
        if chat_id:
            send_telegram_message(chat_id, error_msg)
        return False

    current_state = load_state()
    new_state = {}
    available_locations = []
    errors = []
    account_locked = False  # Флаг блокировки аккаунта

    # Проверяем города последовательно
    for city, config in VISA_CENTERS.items():
        # Если аккаунт заблокирован, прерываем цикл
        if account_locked:
            break
            
        # Получаем список подкатегорий
        subcategories = config.get("subcategories", [])
        
        # Если нет подкатегорий, проверяем только базовую категорию
        if not subcategories:
            subcategories = [""]
        
        # Проверяем каждую подкатегорию
        for subcat in subcategories:
            key = f"{city}|{subcat}" if subcat else f"{city}|"
            
            # Для ручных проверок отправляем прогресс
            if chat_id:
                progress = f"🔍 Проверяю {city} ({subcat})..." if subcat else f"🔍 Проверяю {city}..."
                send_telegram_message(chat_id, progress)
            
            # Задержка между проверками
            delay = random.uniform(2.0, 4.0)
            logger.debug(f"⏱️ Задержка {delay:.2f} сек перед {city} ({subcat})")
            time.sleep(delay)
            
            previous_status = current_state.get(key, "")
            current_status = get_page_status(city, subcat)
            new_state[key] = current_status
            
            if current_status == "account_locked":
                account_locked = True
                errors.append("🔒 Аккаунт заблокирован VFS Global")
                logger.error("🔒 Аккаунт заблокирован, прерываю проверку")
                break  # Прерываем внутренний цикл
            elif current_status in ["error", "service_unavailable"]:
                errors.append(f"{city} ({subcat}) - {current_status}")
                logger.warning(f"⚠️ {city} ({subcat}): ошибка ({current_status})")
            else:
                status_text = "✅ Слоты доступны" if current_status == "slots_available" else "❌ Нет слотов"
                logger.info(f"{city} ({subcat}): {status_text}")
                
                # Проверяем изменения в доступности
                if current_status == "slots_available" and previous_status != "slots_available":
                    display_name = f"{city} ({subcat})" if subcat else city
                    available_locations.append(display_name)
                    logger.info(f"🟢 ОБНАРУЖЕНО ИЗМЕНЕНИЕ в {display_name}!")

    save_state(new_state)

    messages = []
    if errors:
        messages.append("⚠️ Ошибки при проверке:\n" + "\n".join(f"• {error}" for error in errors))
    if available_locations:
        messages.append("🚀 СРОЧНО! Доступны новые слоты:\n" + "\n".join(f"• {loc}" for loc in available_locations))
        encoded_category = urllib.parse.quote(VISA_CATEGORY)
        messages.append(f"🔗 Ссылка: {BASE_URL}/book-an-appointment?category={encoded_category}")
    
    elapsed = time.time() - start_time
    logger.info(f"⌛ Проверка завершена за {elapsed:.2f} сек")
    
    if messages:
        msg = "\n\n".join(messages)
        target_chat_id = chat_id or TELEGRAM_CHAT_ID
        if send_telegram_message(target_chat_id, msg):
            logger.info("✅ Уведомление отправлено")
        else:
            logger.error("❌ Ошибка отправки уведомления")
        return True
    
    # Всегда отправляем результат для ручных запросов
    elif not available_locations and not errors:
        msg = f"😴 Изменений в слотах не обнаружено. Проверка заняла {elapsed:.2f} сек"
        logger.info(msg)
        
        # Для ручных запросов всегда отправляем ответ
        if chat_id:
            send_telegram_message(chat_id, msg)
        # Для фоновых проверок - только при разрешении таймера
        elif should_notify() and TELEGRAM_CHAT_ID:
            send_telegram_message(None, msg)
            
    return False

def background_checker():
    logger.info("🔄 Фоновая проверка запущена")
    while True:
        try:
            if is_within_schedule():
                logger.info("⏱️ Запуск фоновой проверки по расписанию")
                check_slots()
            else:
                logger.info("⏰ Пропуск фоновой проверки вне расписания")
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            logger.error(f"🔥 Критическая ошибка в фоновом потоке: {str(e)}")
            # Отправка уведомления об ошибке
            error_msg = f"🚨 Фоновая проверка упала: {str(e)[:200]}"
            send_telegram_message(TELEGRAM_CHAT_ID, error_msg)
            time.sleep(300)

@app.route("/setwebhook")
def set_webhook():
    # Универсальный подход для любого хостинга
    hostname = os.getenv("EXTERNAL_HOSTNAME")
    if not hostname:
        return Response("❌ EXTERNAL_HOSTNAME не установлен", status=500)
        
    # Удаляем протокол, если есть
    hostname = hostname.replace("https://", "").replace("http://", "")
    
    webhook_url = f"https://{hostname}{WEBHOOK_SECRET_PATH}"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={webhook_url}"
    
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return Response(f"Webhook установлен: {response.json()}", status=200)
    except Exception as e:
        logger.error(f"❌ Ошибка настройки webhook: {str(e)}")
        return Response(f"Ошибка: {str(e)}", status=500)

@app.route(WEBHOOK_SECRET_PATH, methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        update = request.json
        if 'message' in update and 'text' in update['message']:
            chat_id = str(update['message']['chat']['id'])
            text = update['message']['text'].strip()
            
            if text == '/start':
                send_telegram_message(chat_id, f"Бот запущен! Ваш Chat ID: {chat_id}\nКоманды:\n/check - проверить слоты\n/status - текущий статус\n/debug - отладочная информация")
            elif text == '/check':
                # Проверяем, не выполняется ли уже проверка для этого чата
                if chat_id not in active_checks or not active_checks[chat_id].is_set():
                    active_checks[chat_id] = Event()
                    active_checks[chat_id].clear()
                    
                    def check_wrapper(chat_id):
                        try:
                            send_telegram_message(chat_id, "🔍 Запускаю проверку слотов...")
                            result = check_slots(chat_id)
                            if not result:
                                send_telegram_message(chat_id, "ℹ️ Проверка завершена, изменений не обнаружено")
                        except Exception as e:
                            logger.exception(f"🔥 Критическая ошибка при проверке: {str(e)}")
                            send_telegram_message(chat_id, f"⚠️ Критическая ошибка: {str(e)}")
                        finally:
                            if chat_id in active_checks:
                                active_checks[chat_id].set()
                                del active_checks[chat_id]
                    
                    Thread(target=check_wrapper, args=(chat_id,)).start()
                else:
                    send_telegram_message(chat_id, "⏳ Проверка уже выполняется, пожалуйста подождите...")
            elif text == '/status':
                state = load_state()
                report = generate_status_report(state)
                msg = "Текущий статус:\n" + report
                send_telegram_message(chat_id, msg)
            elif text == '/debug':
                debug_info = "🛠️ Debug info:\n"
                debug_info += f"Session: {'active' if session else 'inactive'}\n"
                debug_info += f"Last check: {datetime.now(MINSK_TZ).strftime('%H:%M:%S')}\n"
                debug_info += f"Within schedule: {is_within_schedule()}\n"
                debug_info += f"Active checks: {len(active_checks)}"
                send_telegram_message(chat_id, debug_info)
        return Response("OK", status=200)
    return Response("Invalid content type", status=400)

@app.route("/")
def index():
    try:
        state = load_state()
        status_report = generate_status_report(state)
    except Exception as e:
        status_report = f"Ошибка: {str(e)}"
    
    minsk_time = datetime.now(MINSK_TZ).strftime('%Y-%m-%d %H:%M:%S')
    return render_template('index.html', status_report=status_report, time=minsk_time)

@app.route("/check")
def run_check():
    start_time = time.time()
    logger.info("⏳ Запуск ручной проверки...")
    result = check_slots()
    elapsed = time.time() - start_time
    status = "🟢 Изменения обнаружены!" if result else "🟡 Изменений нет"
    logger.info(f"✅ Проверка завершена за {elapsed:.2f} сек")
    return Response(f"{status}\nВремя выполнения: {elapsed:.2f} сек", mimetype='text/plain')

@app.route("/test")
def test_telegram():
    success = send_telegram_message(TELEGRAM_CHAT_ID, "✅ Тестовое сообщение от бота VFS Monitor!")
    return "Тестовое сообщение отправлено!" if success else "Ошибка отправки"

if __name__ == "__main__":
    Thread(target=background_checker, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)