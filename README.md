# VFS Global News Monitor Bot

Бот для мониторинга изменений на странице новостей VFS Global (Польша).

## Развертывание на Render

1. Создайте новый Web Service в Render
2. Подключите репозиторий GitHub
3. Настройки:
   - **Name:** vfs-monitor-bot
   - **Region:** Frankfurt (EU)
   - **Branch:** main
   - **Build Command:** pip install -r requirements.txt
   - **Start Command:** gunicorn app:app
4. Установите переменные окружения:
   - `TELEGRAM_TOKEN` - токен бота от @BotFather
   - `TELEGRAM_CHAT_ID` - ID вашего чата (можно получить через @userinfobot)
   - `SCRAPINGBEE_API_KEY` - API ключ от scrapingbee.com
   - `RENDER_SERVICE_NAME` = vfs-monitor-bot
   - `PORT` = 10000
   - `CHECK_INTERVAL_MINUTES` = 60 (или другой интервал)
5. Нажмите "Create Web Service"

## Команды бота
- `/start` - информация о боте
- `/status` - текущий статус мониторинга
- `/check` - ручная проверка страницы