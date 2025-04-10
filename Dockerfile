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
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Установка mongodb-database-tools
RUN curl -fsSL https://pgp.mongodb.com/server-7.0.asc | \
    gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg --dearmor && \
    echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg] http://repo.mongodb.org/apt/debian bookworm/mongodb-org/7.0 main" | \
    tee /etc/apt/sources.list.d/mongodb-org-7.0.list && \
    apt-get update && \
    apt-get install -y mongodb-database-tools && \
    rm -rf /var/lib/apt/lists/*

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
