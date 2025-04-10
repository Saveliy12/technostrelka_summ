FROM python:3.11-slim

WORKDIR /app

# Установка необходимых пакетов
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    libffi-dev \
    libjpeg-dev \
    zlib1g-dev \
    git \
    curl \
    mongodb-clients \
    && rm -rf /var/lib/apt/lists/*

# Копирование файлов с зависимостями
COPY requirements.txt .

# Обновление pip и установка базовых зависимостей
RUN pip install --no-cache-dir --upgrade pip wheel setuptools

# Установка зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кодовой базы
COPY . .

# Создаем директории для данных
RUN mkdir -p /app/data /app/logs

# Переменные окружения для отладки
ENV PYTHONUNBUFFERED=1
ENV DEBUG=True

# Указываем, что контейнер слушает порты
EXPOSE 8000
EXPOSE 5000

# Проверка настроек
RUN echo "Checking environment and files:"
RUN ls -la
RUN python -c "import sys; print(f'Python version: {sys.version}')"
RUN python -c "import bot; print('Bot module imported successfully')" || echo "Failed to import bot module"

# Команда запуска приложения с выводом более подробных логов
CMD ["python", "-u", "bot.py"]