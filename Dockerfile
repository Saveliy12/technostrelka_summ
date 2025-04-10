FROM python:3.11-slim

WORKDIR /app

# Установка необходимых пакетов
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Копирование файлов с зависимостями
COPY requirements.txt .

# Установка зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кодовой базы
COPY . .

# Указываем, что контейнер слушает порт 8000
EXPOSE 8000

# Команда запуска приложения
CMD ["python", "main.py"] 