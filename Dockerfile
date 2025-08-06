# Используем официальный образ Python
FROM python:3.11-slim

# Настройка рабочей директории
WORKDIR /app

# Копируем зависимости
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY . .

# Устанавливаем переменные среды по умолчанию
ENV PORT=8000
ENV CHECK_INTERVAL_MINUTES=60

# Запускаем приложение
CMD ["python", "app.py"]