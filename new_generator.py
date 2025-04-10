import os
import base64
from typing import List, Dict, Optional, Any
from pydantic import BaseModel
from jinja2 import Template
from mistralai import Mistral
import re
import json
from enum import Enum
import asyncio
import time

# Попробуем импортировать исключения из библиотеки Mistral AI, если они доступны
try:
    from mistralai.exceptions import AuthenticationError as MistralAuthError
    from mistralai.exceptions import RateLimitError as MistralRateLimitError
    
    # Используем исключения из библиотеки
    class AuthorizationError(MistralAuthError):
        """Исключение при ошибке авторизации API"""
        pass

    class RateLimitError(MistralRateLimitError):
        """Исключение при превышении лимита запросов API"""
        pass
except ImportError:
    # Если импорт не удался, определяем собственные классы исключений
    class AuthorizationError(Exception):
        """Исключение при ошибке авторизации API"""
        pass

    class RateLimitError(Exception):
        """Исключение при превышении лимита запросов API"""
        pass


class RateLimiter:
    """Класс для ограничения частоты запросов к API"""
    
    def __init__(self, requests_per_second=1):
        """
        Инициализация ограничителя запросов
        
        Args:
            requests_per_second: Максимальное количество запросов в секунду (не более 1)
        """
        # Ограничиваем до 1 запроса в секунду максимум, чтобы избежать rate limit
        requests_per_second = min(requests_per_second, 1)
        
        # Устанавливаем минимальный интервал между запросами - не менее 1 секунды
        self.interval = max(1.0, 1.0 / requests_per_second)
        self.last_request_time = 0
        self._lock = asyncio.Lock()
    
    async def acquire(self):
        """Ожидание доступности слота для запроса"""
        async with self._lock:
            current_time = time.time()
            elapsed = current_time - self.last_request_time
            
            # Если с момента последнего запроса прошло меньше времени, чем требуемый интервал,
            # ждем нужное количество времени
            if elapsed < self.interval:
                sleep_time = self.interval - elapsed
                print(f"Ожидание {sleep_time:.2f} сек для соблюдения ограничения запросов API...")
                await asyncio.sleep(sleep_time)
            
            # Обязательно ждем минимум 1 секунду между запросами
            elif self.interval < 1.0:
                print(f"Обязательная задержка в 1 секунду между запросами...")
                await asyncio.sleep(1.0)
                
            self.last_request_time = time.time()
            print(f"Запрос к API разрешен после задержки.")


class DigestStyle(str, Enum):
    """Стили форматирования дайджеста"""
    STANDARD = "standard"  # Стандартный стиль с группировкой по категориям
    COMPACT = "compact"    # Компактный стиль, минимум текста
    MEDIA = "media"        # Медиа-ориентированный стиль (акцент на изображения)
    CARDS = "cards"        # Карточный стиль, каждая новость - отдельная карточка
    ANALYTICS = "analytics"  # Аналитический стиль с фокусом на анализ
    SOCIAL = "social"      # Стиль для социальных сетей с хештегами


class ImageContent(BaseModel):
    """Модель для хранения информации об изображении"""
    path: str  # Путь к изображению
    description: Optional[str] = None  # Описание изображения, полученное от модели


class NewsItem(BaseModel):
    """Модель для хранения информации о новости"""
    raw_text: str  # Исходный текст новости
    image: Optional[ImageContent] = None  # Информация об изображении, если есть
    category: Optional[str] = None  # Категория новости (будет определена при анализе)
    title: Optional[str] = None  # Заголовок новости (будет определен при анализе)
    description: Optional[str] = None  # Описание новости (будет определено при анализе)
    forecast: Optional[str] = None  # Прогноз на основе новости (используется только для аналитики)
    link: Optional[str] = None  # Ссылка на источник
    video_link: Optional[str] = None  # Ссылка на видео (для медиа-стиля)
    hashtags: Optional[List[str]] = None  # Хештеги (для стиля социальных сетей)
    importance: Optional[int] = None  # Важность новости от 1 до 5 (1 - наиболее важная)
    sentiment: Optional[str] = None  # Тональность новости: positive, negative, neutral


class NewsAnalyzer:
    """Анализатор новостей с использованием Mistral AI"""
    
    def __init__(self, api_key: Optional[str] = None, requests_per_second: int = 0.5):
        """
        Инициализация анализатора новостей
        
        Args:
            api_key: API ключ для Mistral AI (если не указан, будет взят из переменных окружения)
            requests_per_second: Максимальное количество запросов в секунду (по умолчанию 0.5, не более 1)
        """
        # Получение API ключа - сначала из параметра, затем из переменных окружения
        self.api_key = api_key
        
        # Если ключ не передан, пробуем получить из переменных окружения
        if not self.api_key:
            # Проверяем оба возможных имени переменной окружения
            self.api_key = os.environ.get("MISTRAL_API_KEYS") or os.environ.get("MISTRAL_API_KEY")
        
        # Обработка ключа из массива (если он в формате JSON)
        if self.api_key and (self.api_key.startswith("[") or self.api_key.startswith("{")):
            try:
                api_keys_data = json.loads(self.api_key)
                # Если это список, берем первый элемент
                if isinstance(api_keys_data, list) and len(api_keys_data) > 0:
                    self.api_key = api_keys_data[0]
                # Если это словарь, смотрим на ключ 'api_key' или берем первое значение
                elif isinstance(api_keys_data, dict):
                    self.api_key = api_keys_data.get('api_key') or next(iter(api_keys_data.values()), None)
            except json.JSONDecodeError:
                # Если не удалось распарсить как JSON, используем как есть
                pass
        
        # Проверяем наличие ключа после всех обработок
        if not self.api_key:
            raise ValueError("API ключ для Mistral AI не найден. Укажите его в параметрах или в переменной окружения MISTRAL_API_KEY или MISTRAL_API_KEYS")
        
        # Очищаем ключ от возможных пробелов и кавычек
        self.api_key = self.api_key.strip().strip('"\'')
        
        # Создаем ограничитель частоты запросов
        self.rate_limiter = RateLimiter(requests_per_second)
        
        # Инициализация клиента с проверкой соединения
        try:
            # Пытаемся инициализировать клиент с предоставленным ключом
            self.client = Mistral(api_key=self.api_key)
            self.text_model = "pixtral-large-latest"  # Начинаем с продвинутой модели
            self.vision_model = "pixtral-large-latest"
            
            # Пробуем сделать тестовый запрос для проверки соединения и авторизации
            test_response = self.client.chat.complete(
                model="mistral-small-latest",  # Используем базовую модель для теста
                messages=[{"role": "user", "content": "Test connection"}],
            )
            
            # Если тест прошел успешно, печатаем сообщение
            print(f"Соединение с Mistral API установлено успешно. Используем модели: {self.text_model}/{self.vision_model}")
            
        except Exception as e:
            print(f"Ошибка при инициализации клиента Mistral AI: {e}")
            error_message = str(e).lower()
            
            # В случае ошибки, переключаемся на самую базовую модель
            try:
                self.client = Mistral(api_key=self.api_key)
                self.text_model = "mistral-small-latest"
                self.vision_model = "mistral-small-latest"
                print(f"Переключились на базовую модель: {self.text_model}")
                
                # Повторно проверяем соединение с базовой моделью
                test_response = self.client.chat.complete(
                    model=self.text_model,
                    messages=[{"role": "user", "content": "Test connection"}],
                )
                
            except Exception as e2:
                print(f"Критическая ошибка при инициализации Mistral API: {e2}")
                error_message = str(e2).lower()
                
                if "401" in error_message or "unauthorized" in error_message or "authentication" in error_message:
                    print("Ошибка авторизации API: Проверьте правильность API ключа.")
                    raise AuthorizationError(f"Ошибка авторизации API: {e2}")
                elif "429" in error_message or "too many requests" in error_message or "rate limit" in error_message:
                    print("Ошибка API: Слишком много запросов. Возможно, достигнут лимит запросов.")
                    raise RateLimitError(f"Превышен лимит запросов API: {e2}")
                else:
                    raise ValueError(f"Не удалось установить соединение с Mistral API: {e2}")
    
    def encode_image(self, image_path: str) -> Optional[str]:
        """
        Кодирует изображение в формат base64
        
        Args:
            image_path: Путь к изображению
            
        Returns:
            Закодированное изображение в формате base64 или None в случае ошибки
        """
        try:
            with open(image_path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode('utf-8')
        except FileNotFoundError:
            print(f"Ошибка: Файл {image_path} не найден.")
            return None
        except Exception as e:
            print(f"Ошибка при кодировании изображения: {e}")
            return None
    
    async def analyze_image_async(self, image_path: str, style: DigestStyle = DigestStyle.STANDARD) -> str:
        """
        Асинхронно анализирует изображение с помощью Mistral AI
        
        Args:
            image_path: Путь к изображению
            style: Стиль анализа изображения
            
        Returns:
            Описание изображения или пустая строка в случае ошибки
        """
        try:
            base64_image = self.encode_image(image_path)
            
            if not base64_image:
                return ""
            
            # Ожидаем доступности слота для запроса - обязательно ждем перед каждым запросом
            print(f"Запрашиваем разрешение на запрос к API для анализа изображения...")
            await self.rate_limiter.acquire()
            
            # Настраиваем промпт в зависимости от стиля
            if style == DigestStyle.MEDIA:
                image_prompt = "Проанализируй это изображение в контексте экономических новостей. Опиши детально, что на нём изображено и как это относится к экономике. Используй 2-3 предложения."
            elif style == DigestStyle.COMPACT:
                image_prompt = "Опиши это экономическое изображение в 3-5 словах."
            elif style == DigestStyle.ANALYTICS:
                image_prompt = "Проанализируй графики или экономические данные на этом изображении. Выдели ключевые тренды и цифры."
            elif style == DigestStyle.SOCIAL:
                image_prompt = "Опиши это изображение для поста в социальной сети об экономике. Используй яркие, привлекающие внимание формулировки."
            else:  # STANDARD, CARDS и другие
                image_prompt = "Проанализируй это изображение в контексте экономических новостей. Выдели ключевую информацию очень кратко, в 5-7 словах."
            
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": image_prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": f"data:image/jpeg;base64,{base64_image}" 
                        }
                    ]
                }
            ]
            
            # Вызываем API с системой автоматических повторов
            async def make_api_call():
                chat_response = self.client.chat.complete(
                    model=self.vision_model,
                    messages=messages
                )
                return chat_response
            
            try:
                response = await self.retry_with_backoff(make_api_call)
                return response.choices[0].message.content
            except Exception as api_err:
                error_message = str(api_err).lower()
                if "401" in error_message or "unauthorized" in error_message or "authentication" in error_message:
                    raise AuthorizationError(f"Ошибка авторизации API: {api_err}")
                elif "429" in error_message or "too many requests" in error_message or "rate limit" in error_message:
                    raise RateLimitError(f"Превышен лимит запросов API: {api_err}")
                else:
                    raise api_err  # Пробрасываем другие ошибки дальше
        except (AuthorizationError, RateLimitError) as e:
            print(f"Ошибка API при анализе изображения: {e}")
            return "Не удалось проанализировать изображение из-за ограничений API."
        except Exception as e:
            print(f"Ошибка при анализе изображения: {e}")
            return ""
    
    def analyze_image(self, image_path: str, style: DigestStyle = DigestStyle.STANDARD) -> str:
        """
        Синхронный метод для анализа изображения (для обратной совместимости)
        
        Args:
            image_path: Путь к изображению
            style: Стиль анализа изображения
            
        Returns:
            Описание изображения или пустая строка в случае ошибки
        """
        try:
            # Проверяем, запущен ли уже event loop
            try:
                loop = asyncio.get_running_loop()
                # Если мы здесь, значит event loop уже запущен - используем threading для запуска асинхронного кода
                import threading
                import queue
                
                result_queue = queue.Queue()
                
                def run_in_thread():
                    try:
                        # Создаем новый event loop для потока
                        new_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(new_loop)
                        # Запускаем асинхронную функцию
                        result = new_loop.run_until_complete(self.analyze_image_async(image_path, style))
                        result_queue.put(result)
                    except Exception as e:
                        print(f"Ошибка в потоке при анализе изображения: {e}")
                        result_queue.put("")
                
                # Запускаем асинхронный код в отдельном потоке
                thread = threading.Thread(target=run_in_thread)
                thread.start()
                thread.join()  # Ждем завершения потока
                
                # Получаем результат
                return result_queue.get()
                
            except RuntimeError:
                # Event loop не запущен, используем стандартный подход
                return asyncio.run(self.analyze_image_async(image_path, style))
                
        except Exception as e:
            print(f"Ошибка при синхронном анализе изображения: {e}")
            return ""
    
    def extract_json_from_text(self, text: str) -> Dict[str, Any]:
        """Извлекает JSON из текстового ответа модели"""
        # Ищем JSON в ответе (между ```json и ```)
        json_match = re.search(r'```(?:json)?\n?(.*?)\n?```', text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Если нет маркеров разметки, пробуем весь текст
            json_str = text
        
        # Чистим от лишних символов
        json_str = json_str.strip()
        
        try:
            # Попытка парсинга JSON
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"Ошибка при парсинге JSON: {e}")
            print(f"Исходный текст: {json_str}")
            return {}
    
    async def retry_with_backoff(self, func, max_retries=3, initial_delay=5.0):
        """
        Выполняет функцию с автоматическим повтором при ошибке превышения лимита запросов
        
        Args:
            func: Функция для выполнения (должна быть корутиной)
            max_retries: Максимальное количество попыток
            initial_delay: Начальная задержка перед повторной попыткой в секундах
            
        Returns:
            Результат выполнения функции
        """
        retries = 0
        current_delay = initial_delay
        
        while True:
            try:
                return await func()
            except RateLimitError as e:
                retries += 1
                if retries > max_retries:
                    print(f"Превышено максимальное количество попыток ({max_retries}). Ошибка: {e}")
                    raise
                
                print(f"Превышен лимит запросов (попытка {retries}/{max_retries}). "
                      f"Ожидание {current_delay} секунд перед повторной попыткой...")
                
                await asyncio.sleep(current_delay)
                # Увеличиваем время ожидания для следующей попытки (экспоненциальная задержка)
                current_delay *= 2
    
    async def analyze_news_async(self, raw_text: str, image_path: Optional[str] = None, style: DigestStyle = DigestStyle.STANDARD, video_link: Optional[str] = None) -> Dict[str, Any]:
        """
        Асинхронно анализирует новость и создает структурированный объект
        
        Args:
            raw_text: Исходный текст новости
            image_path: Путь к изображению (если есть)
            style: Стиль анализа и отображения новости
            video_link: Ссылка на видео (если есть)
            
        Returns:
            Словарь с результатами анализа
        """
        try:
            raw_text = raw_text.strip()
            
            # Ожидаем доступности слота для запроса - обязательно ждем перед каждым запросом
            print(f"Запрашиваем разрешение на запрос к API для анализа новости...")
            await self.rate_limiter.acquire()
            
            # Системный промпт для анализа новости
            system_prompt = """Ты - эксперт по анализу новостей для профессиональных финансовых дайджестов.
Твоя задача - проанализировать новость и предоставить следующую информацию:
1. Основная категория новости (выбери одну): Экономика, Финансы, Рынки, Регулирование, Технологии, Компании, Международные отношения, Макроэкономика, Инвестиции
2. Создай профессиональный и лаконичный заголовок для новости (не более 100 символов)
3. Напиши краткое описание новости (не более 250 символов)
4. Детально объясни, почему эта новость важна для бизнеса и инвесторов (до 600 символов)

Отвечай ТОЛЬКО в указанном JSON-формате. Никаких дополнительных комментариев или пояснений."""

            # Инструкция для API
            prompt = f"""Вот текст новости для анализа:
---
{raw_text}
---

Пожалуйста, проанализируй эту новость и предоставь информацию согласно требованиям. Ответ должен быть в строгом JSON-формате:
{{
  "category": "Категория",
  "title": "Заголовок",
  "description": "Краткое описание",
  "importance": "Объяснение важности"
}}"""

            # Вызываем API с системой автоматических повторов
            async def make_api_call():
                response = self.client.chat.complete(
                    model=self.text_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=1000
                )
                return response
            
            try:
                response = await self.retry_with_backoff(make_api_call)
                
                response_text = response.choices[0].message.content.strip()
                
                # Извлекаем JSON из ответа (он может быть обернут в тройные кавычки или блоки кода)
                match = re.search(r'```(?:json)?\s*({[\s\S]*?})\s*```|({[\s\S]*?})', response_text)
                if match:
                    json_str = match.group(1) or match.group(2)
                else:
                    json_str = response_text
                    
                result = json.loads(json_str)
                
                # Добавляем исходный текст и пути к медиа-файлам
                result["raw_text"] = raw_text
                result["image_path"] = image_path
                result["video_link"] = video_link
                
                return result
            except Exception as api_err:
                error_message = str(api_err).lower()
                if "401" in error_message or "unauthorized" in error_message or "authentication" in error_message:
                    raise AuthorizationError(f"Ошибка авторизации API: {api_err}")
                elif "429" in error_message or "too many requests" in error_message or "rate limit" in error_message:
                    raise RateLimitError(f"Превышен лимит запросов API: {api_err}")
                else:
                    raise api_err  # Пробрасываем другие ошибки дальше
            
        except (AuthorizationError, RateLimitError) as e:
            print(f"Ошибка авторизации API Mistral: {e}")
            print("Используем заглушку для анализа новости")
            # Создаем заглушку для результата
            return {
                "raw_text": raw_text,
                "category": "Экономика",
                "title": raw_text[:50] + "..." if len(raw_text) > 50 else raw_text,
                "description": raw_text[:100] + "..." if len(raw_text) > 100 else raw_text,
                "importance": "Не удалось проанализировать из-за ошибки API.",
                "image_path": image_path,
                "video_link": video_link
            }
        except Exception as e:
            print(f"Ошибка при анализе новости: {e}")
            
            # Создаем заглушку для результата при ошибке
            return {
                "raw_text": raw_text,
                "category": "Экономика",
                "title": raw_text[:50] + "..." if len(raw_text) > 50 else raw_text,
                "description": raw_text[:100] + "..." if len(raw_text) > 100 else raw_text,
                "importance": f"Ошибка анализа: {str(e)}",
                "image_path": image_path,
                "video_link": video_link
            }
    
    def analyze_news(self, raw_text: str, image_path: Optional[str] = None, 
                    style: DigestStyle = DigestStyle.STANDARD, video_link: Optional[str] = None) -> Dict[str, Any]:
        """
        Синхронно анализирует новость и создает структурированный объект
        
        Args:
            raw_text: Исходный текст новости
            image_path: Путь к изображению (если есть)
            style: Стиль анализа и отображения новости
            video_link: Ссылка на видео (если есть)
            
        Returns:
            Словарь с результатами анализа
        """
        try:
            # Проверяем, запущен ли уже event loop
            try:
                loop = asyncio.get_running_loop()
                # Если мы здесь, значит event loop уже запущен - используем threading для запуска асинхронного кода
                import threading
                import queue
                
                result_queue = queue.Queue()
                
                def run_in_thread():
                    try:
                        # Создаем новый event loop для потока
                        new_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(new_loop)
                        # Запускаем асинхронную функцию
                        result = new_loop.run_until_complete(self.analyze_news_async(raw_text, image_path, style, video_link))
                        result_queue.put(result)
                    except Exception as e:
                        print(f"Ошибка в потоке при анализе новости: {e}")
                        result_queue.put({
                            "raw_text": raw_text,
                            "category": "Экономика",
                            "title": raw_text[:50] + "..." if len(raw_text) > 50 else raw_text,
                            "description": raw_text[:100] + "..." if len(raw_text) > 100 else raw_text,
                            "importance": f"Ошибка анализа в потоке: {str(e)}",
                            "image_path": image_path,
                            "video_link": video_link
                        })
                
                # Запускаем асинхронный код в отдельном потоке
                thread = threading.Thread(target=run_in_thread)
                thread.start()
                thread.join()  # Ждем завершения потока
                
                # Получаем результат
                return result_queue.get()
                
            except RuntimeError:
                # Event loop не запущен, используем стандартный подход
                return asyncio.run(self.analyze_news_async(raw_text, image_path, style, video_link))
                
        except Exception as e:
            print(f"Ошибка при синхронном анализе новости: {e}")
            return {
                "raw_text": raw_text,
                "category": "Экономика",
                "title": raw_text[:50] + "..." if len(raw_text) > 50 else raw_text,
                "description": raw_text[:100] + "..." if len(raw_text) > 100 else raw_text,
                "importance": f"Ошибка синхронного анализа: {str(e)}",
                "image_path": image_path,
                "video_link": video_link
            }
    
    async def generate_overall_analysis_async(self, news_items: List[Dict[str, Any]], style: DigestStyle = DigestStyle.STANDARD) -> str:
        """
        Асинхронно создает общий анализ и прогноз на основе набора новостей
        
        Args:
            news_items: Список проанализированных новостей
            style: Стиль анализа
            
        Returns:
            Текст с общим анализом и прогнозом
        """
        try:
            if not news_items:
                return "📊 **АНАЛИЗ ТЕНДЕНЦИЙ**\n\nНедостаточно данных для анализа. Для создания общего анализа требуются новости."
            
            # Ожидаем доступности слота для запроса - обязательно ждем перед каждым запросом
            print(f"Запрашиваем разрешение на запрос к API для создания общего анализа...")
            await self.rate_limiter.acquire()
            
            # Создаем краткое резюме новостей
            news_summary = "\n\n".join([
                f"**{item.get('title', 'Без заголовка')}**\n{item.get('description', 'Нет описания')}"
                for item in news_items
            ])
            
            # Системный промпт
            system_prompt = """Ты - опытный финансовый аналитик, составляющий глубокий анализ новостей для профессионального делового дайджеста.
Твоя задача - проанализировать предоставленную сводку новостей и составить экспертное заключение:

1. Выдели 2-3 ключевых тренда, которые можно распознать в указанных новостях
2. Объясни, как эти события влияют на экономическую ситуацию и финансовые рынки
3. Дай аргументированный прогноз дальнейшего развития ситуации и возможных последствий
4. При необходимости, укажи потенциальные риски и возможности для бизнеса и инвесторов

Твой анализ должен быть:
- Профессиональным и глубоким, с пониманием фундаментальных экономических механизмов
- Нейтральным и объективным, основанным на фактах
- Структурированным, с ясной логикой и разделением на разделы с подзаголовками
- Полезным для принятия стратегических решений

НЕ НАЧИНАЙ свой ответ с заголовка "Аналитический обзор" или других заголовков - они уже будут добавлены в шаблоне дайджеста."""

            # Формируем запрос с примерами новостей
            prompt = f"""Вот сводка последних значимых бизнес-новостей:

{news_summary}

Пожалуйста, проанализируй эти новости и предоставь подробный анализ текущей ситуации и потенциальных последствий."""

            # Вызываем API с системой автоматических повторов
            async def make_api_call():
                response = self.client.chat.complete(
                    model=self.text_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=2000
                )
                return response
            
            try:
                response = await self.retry_with_backoff(make_api_call)
                
                # Добавляем заголовок к контенту в зависимости от стиля
                api_response = response.choices[0].message.content.strip()
                if style == DigestStyle.STANDARD:
                    return f"📊 **АНАЛИЗ ТЕНДЕНЦИЙ**\n\n{api_response}"
                elif style == DigestStyle.ANALYTICS:
                    return f"🔍 **ЭКОНОМИЧЕСКИЙ АНАЛИЗ**\n\n{api_response}"
                elif style == DigestStyle.MEDIA:
                    return f"📊 **ИТОГИ И ПРОГНОЗ**\n\n{api_response}"
                elif style == DigestStyle.SOCIAL:
                    return f"💎 **АНАЛИЗ**\n\n{api_response}"
                elif style == DigestStyle.CARDS:
                    return f"📝 **ОБЩИЙ ВЫВОД**\n\n{api_response}"
                else:  # DigestStyle.COMPACT
                    return f"💡 **АНАЛИЗ**\n\n{api_response}"
            except Exception as api_err:
                error_message = str(api_err).lower()
                if "401" in error_message or "unauthorized" in error_message or "authentication" in error_message:
                    raise AuthorizationError(f"Ошибка авторизации API: {api_err}")
                elif "429" in error_message or "too many requests" in error_message or "rate limit" in error_message:
                    raise RateLimitError(f"Превышен лимит запросов API: {api_err}")
                else:
                    raise api_err  # Пробрасываем другие ошибки дальше
            
        except (AuthorizationError, RateLimitError) as e:
            print(f"Ошибка API при создании общего анализа: {e}")
            return "📊 **АНАЛИЗ ТЕНДЕНЦИЙ**\n\nВ настоящее время сервис аналитики недоступен из-за технических ограничений. Пожалуйста, ознакомьтесь с новостями самостоятельно и повторите попытку позже."
            
        except Exception as e:
            print(f"Ошибка при создании общего анализа: {e}")
            return f"📊 **АНАЛИЗ ТЕНДЕНЦИЙ**\n\nАнализ текущих новостей показывает смешанную экономическую картину. Следите за дальнейшим развитием событий для принятия взвешенных финансовых решений."

    def generate_overall_analysis(self, news_items: List[Dict[str, Any]], style: DigestStyle = DigestStyle.STANDARD) -> str:
        """
        Синхронно создает общий анализ и прогноз на основе набора новостей
        
        Args:
            news_items: Список проанализированных новостей
            style: Стиль анализа
            
        Returns:
            Текст с общим анализом и прогнозом
        """
        try:
            # Проверяем, запущен ли уже event loop
            try:
                loop = asyncio.get_running_loop()
                # Если мы здесь, значит event loop уже запущен - используем threading для запуска асинхронного кода
                import threading
                import queue
                
                result_queue = queue.Queue()
                
                def run_in_thread():
                    try:
                        # Создаем новый event loop для потока
                        new_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(new_loop)
                        # Запускаем асинхронную функцию
                        result = new_loop.run_until_complete(self.generate_overall_analysis_async(news_items, style))
                        result_queue.put(result)
                    except Exception as e:
                        print(f"Ошибка в потоке при создании общего анализа: {e}")
                        result_queue.put("📊 **АНАЛИЗ ТЕНДЕНЦИЙ**\n\nПроизошла ошибка при обработке данных. Пожалуйста, повторите попытку позже.")
                
                # Запускаем асинхронный код в отдельном потоке
                thread = threading.Thread(target=run_in_thread)
                thread.start()
                thread.join()  # Ждем завершения потока
                
                # Получаем результат
                return result_queue.get()
                
            except RuntimeError:
                # Event loop не запущен, используем стандартный подход
                return asyncio.run(self.generate_overall_analysis_async(news_items, style))
                
        except Exception as e:
            print(f"Ошибка при синхронном создании общего анализа: {e}")
            return f"📊 **АНАЛИЗ ТЕНДЕНЦИЙ**\n\nПроизошла ошибка при создании общего анализа: {str(e)}"


class DigestGenerator:
    """Генератор дайджеста экономических новостей для Telegram"""
    
    # Словарь для хранения эмодзи соответствующих категориям
    CATEGORY_EMOJI = {
        "Финансы": "💰",
        "Рынки": "📈",
        "Макроэкономика": "🌐",
        "Компании": "🏢",
        "Регулирование": "⚖️",
        "Криптовалюты": "🪙",
        "Инвестиции": "💼",
        "Банки": "🏦",
        "Экономика": "📊",
        "Недвижимость": "🏗️",
        "Энергетика": "⚡",
        "Технологии": "💻"
    }
    
    # Шаблоны для разных стилей оформления
    TEMPLATES = {
        DigestStyle.STANDARD: """Экономический дайджест (#{{ digest_number }})

{% for category, items in news_by_category.items() %}
{{ category }}
{% for item in items %}
- {{ item.title }} {% if item.link %}({{ item.link }}){% endif %} — {{ item.description }}{% if item.image_description %} 🖼 {{ item.image_description }}{% endif %}{% if item.video_link %} 🎬 {{ item.video_link }}{% endif %}
{% endfor %}

{% endfor %}
{{ overall_analysis }}""",

        DigestStyle.COMPACT: """Дайджест #{{ digest_number }}

{% for category, items in news_by_category.items() %}
{{ category }}
{% for item in items %}
• {{ item.title }} — {{ item.description }}
{% endfor %}

{% endfor %}
{{ overall_analysis }}""",

        DigestStyle.MEDIA: """📰 ЭКОНОМИЧЕСКИЙ МЕДИА-ДАЙДЖЕСТ #{{ digest_number }} 📰

{% for category, items in news_by_category.items() %}
{{ category }}
{% for item in items %}
🔹 {{ item.title }}
{{ item.description }}
{% if item.image_description %}🖼️ {{ item.image_description }}{% endif %}
{% if item.video_link %}🎬 {{ item.video_link }}{% endif %}
{% if item.media_caption %}💬 {{ item.media_caption }}{% endif %}
{{ "—"*30 }}
{% endfor %}

{% endfor %}
{{ overall_analysis }}""",

        DigestStyle.CARDS: """ЭКОНОМИЧЕСКИЙ ДАЙДЖЕСТ #{{ digest_number }}

{% for category, items in news_by_category.items() %}
=== {{ category }} ===
{% for item in items %}
┌─────────────────────────────┐
│ {{ item.title }} {% if item.importance and item.importance|int > 0 %}[{{ "❗" * (item.importance|int) }}]{% endif %}
│ 
│ {{ item.description }}
│ {% if item.sentiment == "positive" %}📈 Позитивно{% elif item.sentiment == "negative" %}📉 Негативно{% else %}📊 Нейтрально{% endif %}
│ {% if item.image_description %}🖼 {{ item.image_description }}{% endif %}
└─────────────────────────────┘
{% endfor %}

{% endfor %}
{{ overall_analysis }}""",

        DigestStyle.ANALYTICS: """АНАЛИТИЧЕСКИЙ ЭКОНОМИЧЕСКИЙ ДАЙДЖЕСТ #{{ digest_number }}

{% for category, items in news_by_category.items() %}
{{ category }}
{% for item in items %}
#{{ loop.index }} {{ item.title }} {% if item.importance %}[важность: {{ item.importance }}/5]{% endif %}
📊 {{ item.description }}
{% if item.sentiment == "positive" %}📈 Позитивная динамика{% elif item.sentiment == "negative" %}📉 Негативная динамика{% else %}⚖️ Нейтральная динамика{% endif %}
{% if item.image_description %}📊 {{ item.image_description }}{% endif %}
{% endfor %}

{% endfor %}
{{ overall_analysis }}""",

        DigestStyle.SOCIAL: """#ЭкономическийДайджест №{{ digest_number }}

{% for category, items in news_by_category.items() %}
{{ category }}
{% for item in items %}
🔥 {{ item.title }}
{{ item.description }}
{% if item.hashtags %}{{ " ".join(["#" + tag.replace(" ", "") for tag in item.hashtags]) }}{% endif %}
{% if item.image_description %}📸 {{ item.image_description }}{% endif %}
{% if item.video_link %}📱 {{ item.video_link }}{% endif %}
{% endfor %}

{% endfor %}
{{ overall_analysis }}

#экономика #финансы #инвестиции"""
    }
    
    def __init__(self, style: DigestStyle = DigestStyle.STANDARD, template_string: Optional[str] = None, use_emoji: bool = True):
        """
        Инициализация генератора дайджеста
        
        Args:
            style: Стиль форматирования дайджеста
            template_string: Шаблон для форматирования дайджеста (опционально)
            use_emoji: Добавлять ли эмодзи к категориям
        """
        # Сохраняем стиль
        self.style = style
        self.use_emoji = use_emoji
        self.analyzer = NewsAnalyzer()
        
        # Сохраняем шаблон и его исходный текст
        if template_string:
            self.template = Template(template_string)
            self.template_source = template_string
        else:
            template_text = self.TEMPLATES.get(style, self.TEMPLATES[DigestStyle.STANDARD])
            self.template = Template(template_text)
            self.template_source = template_text
    
    def _add_emoji_to_category(self, category: str) -> str:
        """Добавляет эмодзи к названию категории"""
        if not self.use_emoji:
            return category
            
        emoji = self.CATEGORY_EMOJI.get(category, "📌")
        return f"{emoji} {category}"
    
    def generate_digest(self, analyzed_news: List[Dict[str, Any]], digest_number: int, style: Optional[DigestStyle] = None) -> str:
        """
        Генерирует дайджест новостей по шаблону
        
        Args:
            analyzed_news: Список проанализированных новостей
            digest_number: Номер дайджеста
            style: Стиль форматирования (если отличается от стиля в конструкторе)
            
        Returns:
            Отформатированный текст дайджеста для Telegram
        """
        # Используем переданный стиль или стиль по умолчанию
        current_style = style or self.style
        
        # Если передан стиль, отличный от установленного при инициализации,
        # и не был передан кастомный шаблон, обновляем шаблон
        if style and style != self.style and self.template_source == self.TEMPLATES.get(self.style, self.TEMPLATES[DigestStyle.STANDARD]):
            template_text = self.TEMPLATES.get(style, self.TEMPLATES[DigestStyle.STANDARD])
            self.template = Template(template_text)
            self.template_source = template_text
            self.style = style
        
        # Группировка новостей по категориям
        news_by_category: Dict[str, List[Dict[str, Any]]] = {}
        
        for news in analyzed_news:
            category = news.get("category", "Экономика")
            
            # Добавляем эмодзи к категории при необходимости
            formatted_category = self._add_emoji_to_category(category)
            
            if formatted_category not in news_by_category:
                news_by_category[formatted_category] = []
                
            news_by_category[formatted_category].append(news)
        
        # Создаем общий анализ и прогноз с учетом выбранного стиля
        overall_analysis = self.analyzer.generate_overall_analysis(analyzed_news, current_style)
        
        # Генерация дайджеста по шаблону
        return self.template.render(
            news_by_category=news_by_category,
            digest_number=digest_number,
            overall_analysis=overall_analysis
        )

    @classmethod
    def get_available_styles(cls) -> List[str]:
        """Возвращает список доступных стилей форматирования"""
        return [style.value for style in DigestStyle]


# Пример использования
if __name__ == "__main__":
    # Создаем анализатор новостей
    analyzer = NewsAnalyzer()
    
    # Пример новостей для анализа
    raw_news = [["""Заместитель Председателя Банка России Алексей Заботкин представил результаты Всероссийского обследования домохозяйств по потребительским финансам 2024 года, а также рассказал о результатах первичного анализа опросных данных. 

Вот несколько выводов из доклада (https://www.cbr.ru/press/event/?id=23496):

🔵 Номинальные доходы на человека в 2022-2024 гг. выросли во всех доходных группах

🔵 Реальные доходы на человека значимо выросли с 2022 по 2024 гг. у 65% домохозяйств

🔵 Доходы росли быстрее расходов

🔵 Сбережения домохозяйств увеличились

🔵 Доля домохозяйств, у которых есть финансовые активы, выросла с 72,9% до 75,5%

🔵 Доля домохозяйств с обязательствами сохраняется, средний размер обязательств растет

🔵 Спрос на кредиты снизился. В группе с наибольшим ростом доходов — рост спроса на ипотеку 

🔵 Чем выше оценка перспектив экономики и материального положения, тем ниже инфляционные ожидания

🔵 Высокие инфляционные ожидания — у тех, кто не имеет сбережений и предпочитает тратить деньги

🔵 Чем выше уровень финансовой грамотности, тем ниже инфляционные ожидания.

Как и два года назад после выхода прошлой волны обследования, приглашаем аналитиков и исследователей использовать эти данные в своей работе""", "/Users/stepan/Documents/вввв.jpg"],
        ["Акции компании Tesla выросли на 8.5% после публикации квартального отчета. Прибыль компании превысила ожидания аналитиков на 15%, а выручка составила $25.5 млрд.", None, "https://youtu.be/example"],
        ["Министерство финансов США разместило 10-летние казначейские облигации на сумму $24 млрд под 4.2% годовых. Спрос превысил предложение в 2.4 раза."]
    ]
    
    # Выбираем стиль для примера
    selected_style = DigestStyle.STANDARD
    
    # Анализируем каждую новость с учетом выбранного стиля
    analyzed_news = []
    for news_item in raw_news:
        if len(news_item) > 2 and news_item[2]:  # Если есть видео
            result = analyzer.analyze_news(
                news_item[0], 
                image_path=news_item[1] if len(news_item) > 1 and news_item[1] else None,
                style=selected_style,
                video_link=news_item[2]
            )
        else:
            result = analyzer.analyze_news(
                news_item[0], 
                image_path=news_item[1] if len(news_item) > 1 and news_item[1] else None,
                style=selected_style
            )
        analyzed_news.append(result)
    
    # Создаем генератор дайджеста с выбранным стилем
    generator = DigestGenerator(style=selected_style)
    
    # Генерируем дайджест
    digest = generator.generate_digest(analyzed_news, 1)
    
    # Выводим результат
    print(digest)
    
    # Пример смены стиля для того же набора новостей
    print("\n" + "="*50 + "\n")
    digest_media = generator.generate_digest(analyzed_news, 1, style=DigestStyle.MEDIA)
    print(digest_media) 
