# Используем официальный образ Python
FROM python:3.11-slim-bullseye

# Устанавливаем переменные окружения
ENV PYTHONUNBUFFERED=1 \
    TZ=Europe/Minsk \
    PYTHONDONTWRITEBYTECODE=1

# Устанавливаем системные зависимости и очищаем кэш
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    libjpeg-dev \
    zlib1g-dev \
    tzdata && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Создаем и переходим в рабочую директорию
WORKDIR /app

# Копируем зависимости и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY . .

# Экспонируем порт для Flask
EXPOSE 8000

# Запускаем приложение
CMD ["sh", "-c", "python -u app.py"]D