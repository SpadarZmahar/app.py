# -*- coding: utf-8 -*-
import os
import logging
import time
import hashlib
import random
import json
from threading import Thread
from bs4 import BeautifulSoup
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, CallbackContext

# --- НОВЫЕ ИМПОРТЫ ---
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# --- ОБНОВЛЕННЫЕ НАСТРОЙКИ ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - [%(levelname)s] - %(message)s",
)
logger = logging.getLogger(__name__)

# Убираем логи для неважных модулей
for lib in ['urllib3', 'selenium', 'undetected_chromedriver']:
    logging.getLogger(lib).setLevel(logging.WARNING)

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
app = Flask(__name__)
last_news_hash = None
DRIVER_INSTANCES = {}
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{}.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/{}.0 Safari/605.1.15"
]

# --- ОБНОВЛЕННЫЕ ФУНКЦИИ ---
def get_driver(thread_id):
    """Инициализация скрытого браузера с уникальными параметрами"""
    if thread_id in DRIVER_INSTANCES:
        return DRIVER_INSTANCES[thread_id]
    
    chrome_version = random.randint(110, 125)
    user_agent = random.choice(USER_AGENTS).format(chrome_version)
    
    options = uc.ChromeOptions()
    options.add_argument(f"user-agent={user_agent}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-web-security")
    options.add_argument("--allow-running-insecure-content")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    
    # Для серверного режима
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1920,1080")
    
    driver = uc.Chrome(
        options=options,
        version_main=chrome_version,
        enable_cdp_events=True
    )
    
    # Запускаем с настройками stealth
    driver.execute_cdp_cmd(
        "Network.setUserAgentOverride",
        {
            "userAgent": user_agent,
            "platform": "Win32",
            "userAgentMetadata": {
                "brands": [
                    {"brand": "Chromium", "version": str(chrome_version)},
                    {"brand": "Google Chrome", "version": str(chrome_version)},
                    {"brand": "Not=A?Brand", "version": "24"}
                ],
                "fullVersionList": [
                    {"brand": "Chromium", "version": str(chrome_version)},
                    {"brand": "Google Chrome", "version": str(chrome_version)},
                    {"brand": "Not=A?Brand", "version": "24"}
                ],
                "platform": "Windows",
                "platformVersion": "10.0.0",
                "architecture": "x86",
                "model": "",
                "mobile": False
            }
        }
    )
    
    # Скрываем WebDriver
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    DRIVER_INSTANCES[thread_id] = driver
    return driver

def fetch_page_content():
    """Получение контента через headless Chrome"""
    thread_id = threading.get_ident()
    driver = get_driver(thread_id)
    
    try:
        driver.get(NEWS_URL)
        
        # Ожидание загрузки основного контента
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.news-container"))
        )
        
        # Прокрутка для имитации поведения пользователя
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight/3);")
        time.sleep(random.uniform(0.5, 1.5))
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(random.uniform(0.7, 2.0))
        
        # Получение обработанного HTML
        page_source = driver.page_source
        soup = BeautifulSoup(page_source, "html.parser")
        
        # Очистка контента (как в вашем оригинале)
        for element in soup(["script", "style", "meta", "link", "nav", "footer"]):
            element.decompose()
        
        page_text = soup.get_text(separator="\n", strip=True)
        page_text = "\n".join(line.strip() for line in page_text.split("\n") if line.strip())
        
        for phrase in UNWANTED_PHRASES:
            page_text = page_text.replace(phrase, "")
        
        if len(page_text) > MAX_TEXT_LENGTH:
            page_text = page_text[:MAX_TEXT_LENGTH] + "\n\n... (текст обрезан)"
        
        return page_text
        
    except TimeoutException:
        logger.error("Таймаут при загрузке страницы")
        return None
    except Exception as e:
        logger.error(f"Ошибка в fetch_page_content: {str(e)}")
        return None

# --- ОСТАЛЬНОЙ КОД ОСТАЕТСЯ ПРЕЖНИМ С КОРРЕКТИРОВКАМИ ---
def background_page_checker():
    """Фоновая проверка с очисткой драйвера"""
    time.sleep(10)
    
    while True:
        try:
            check_news_and_notify()
            
            # Перезапуск драйвера каждые 24 часа
            if time.time() % 86400 < CHECK_INTERVAL_SECONDS:
                thread_id = threading.get_ident()
                if thread_id in DRIVER_INSTANCES:
                    try:
                        DRIVER_INSTANCES[thread_id].quit()
                    except:
                        pass
                    del DRIVER_INSTANCES[thread_id]
            
            time.sleep(CHECK_INTERVAL_SECONDS)
        except Exception as e:
            logger.error(f"Фоновая ошибка: {str(e)}")
            time.sleep(60)

# Добавляем в конец
@app.route('/shutdown', methods=['POST'])
def shutdown():
    """Корректное завершение работы"""
    for thread_id, driver in DRIVER_INSTANCES.items():
        try:
            driver.quit()
        except:
            pass
    os.kill(os.getpid(), 9)
    return 'Server shutting down...'