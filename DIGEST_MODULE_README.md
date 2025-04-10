# Модуль генерации экономических дайджестов

Этот модуль представляет собой веб-интерфейс для генерации экономических дайджестов с использованием искусственного интеллекта. Он может быть запущен как отдельное приложение или интегрирован в существующее Flask-приложение.

## Основные возможности

- Генерация дайджестов экономических новостей из Telegram-каналов
- Выбор различных стилей оформления дайджеста
- Аналитика трендов на основе обработанных новостей
- Управление источниками новостей
- Копирование и скачивание сгенерированных дайджестов
- Анимированный и отзывчивый пользовательский интерфейс

## Структура модуля

```
digest_module/
├── templates/                       # HTML шаблоны
│   └── digest_index.html            # Основной шаблон страницы
├── static/                          # Статические файлы
│   ├── css/                         # CSS стили
│   │   └── styles.css               # Основные стили приложения
│   ├── js/                          # JavaScript файлы
│   │   └── app.js                   # Основной скрипт приложения
│   └── img/                         # Изображения (при необходимости)
├── web_digest_module.py             # Самостоятельное веб-приложение
└── digest_module_init.py            # Модуль для интеграции в существующее приложение
```

## Требования

Для работы модуля необходимы следующие зависимости:

- Python 3.7+
- Flask
- Mistral AI API ключ
- MongoDB для хранения источников

## Использование как отдельное приложение

Запустите `web_digest_module.py` для использования модуля как отдельного приложения:

```python
from web_digest_module import DigestWebModule

if __name__ == "__main__":
    web_module = DigestWebModule(host='0.0.0.0', port=5000)
    web_module.run(debug=True)
```

## Интеграция в существующее Flask приложение

Для интеграции в существующее Flask-приложение используйте `digest_module_init.py`:

```python
from flask import Flask
from digest_module_init import DigestModuleIntegration

app = Flask(__name__)

# Инициализация модуля дайджестов
digest_module = DigestModuleIntegration(app)

@app.route('/')
def index():
    return 'Основное приложение. Перейдите на <a href="/digest">страницу дайджестов</a>'

if __name__ == '__main__':
    app.run(debug=True)
```

После интеграции модуль будет доступен по URL `/digest`.

## Настройка переменных окружения

Создайте файл `.env` с необходимыми переменными окружения:

```
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
MISTRAL_API_KEYS=your_mistral_api_key
MONGODB_URI=mongodb://username:password@host:port/dbname
DEFAULT_NEWS_COUNT=5
DEFAULT_UPDATE_FREQUENCY=24
```

## API эндпоинты

Модуль предоставляет следующие API эндпоинты:

- `GET /api/styles` - получение списка доступных стилей
- `GET /api/sources` - получение списка источников
- `POST /api/sources` - добавление нового источника
- `DELETE /api/sources/<username>` - удаление источника
- `POST /api/generate-digest` - генерация дайджеста

## Пример запроса для генерации дайджеста

```javascript
fetch('/api/generate-digest', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json'
    },
    body: JSON.stringify({
        style: 'standard',
        news_count: 5,
        include_analysis: true
    })
})
.then(response => response.json())
.then(data => console.log(data));
```

## Кастомизация

### Добавление нового стиля дайджеста

Для добавления нового стиля:

1. Расширьте перечисление DigestStyle в `new_generator.py`
2. Добавьте шаблон для нового стиля в `DigestGenerator.TEMPLATES`
3. Обновите CSS стили для нового вида в `static/css/styles.css`

### Настройка внешнего вида

Веб-интерфейс использует Bootstrap 5 и может быть настроен с помощью изменения CSS стилей.

## Решение проблем

### "Не удалось получить новости"

Убедитесь, что:
- В базе данных есть добавленные источники
- Telegram каналы доступны и не заблокированы
- Соединение с Интернетом стабильно

### "Ошибка при анализе новости"

Убедитесь, что:
- API ключ Mistral AI действителен и активен
- Вы не превысили лимит запросов к API
- Формат запросов к API корректен

## Лицензия

MIT 