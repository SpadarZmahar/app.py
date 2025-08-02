# VFS Global Slot Monitor

Бот для мониторинга доступных слотов записи в визовые центры через VFS Global.

## Особенности
- Автоматическая проверка слотов каждые 30 минут
- Уведомления в Telegram при появлении слотов
- Защита от блокировок аккаунта
- Веб-интерфейс для просмотра статуса

## Установка на Railway

1. Создайте новый проект на [Railway](https://railway.app)
2. Добавьте переменные окружения:

   | Переменная          | Обязательно | Пример значения                          | Описание                     |
   |---------------------|-------------|------------------------------------------|------------------------------|
   | `TELEGRAM_TOKEN`    | Да          | `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew` | Токен бота Telegram          |
   | `TELEGRAM_CHAT_ID`  | Да          | `123456789`                              | Ваш Chat ID в Telegram       |
   | `EMAIL`             | Да          | `your@email.com`                         | Логин VFS Global             |
   | `PASSWORD`          | Да          | `your_password`                          | Пароль VFS Global            |
   | `ANTI_CAPTCHA_KEY`  | Нет         | `1234567890abcdef1234567890abcdef`       | Ключ anti-captcha.com        |
   | `SENTRY_DSN`        | Нет         | `https://...@sentry.io/...`              | DSN для Sentry               |
   | `BASE_URL`          | Нет         | `https://visa.vfsglobal.com/blr/ru/pol`  | Базовый URL VFS              |
   | `CHECK_INTERVAL`    | Нет         | `1800`                                   | Интервал проверки (секунды)  |

3. После деплоя установите вебхук: