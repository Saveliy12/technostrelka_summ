import os
import json
import re
from datetime import datetime, timedelta
import pandas as pd
from typing import List, Dict, Set
import aiohttp
import asyncio
from bs4 import BeautifulSoup
import random
import pytz
import base64
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from pathlib import Path
from mistralai import Mistral
import time
import logging
from db_manager import MongoDBManager
import traceback

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Константы
POSTS_TO_ANALYZE = 20  # Количество последних постов для анализа по умолчанию

# Пороговые значения для анализа постов
SIMILARITY_THRESHOLD = 0.6  # Порог для определения похожих постов
MERGE_SIMILARITY_THRESHOLD = 0.65  # Порог для объединения похожих постов
AD_THRESHOLD = 0.5  # Порог для определения рекламных постов
ECONOMICS_RELEVANCE_THRESHOLD = 0.4  # Порог для определения релевантности экономической тематике
AD_FILTER_THRESHOLD = 0.6  # Порог для фильтрации рекламных постов

# Веса для оценки источника
SOURCE_WEIGHTS = {
    'subscribers': 0.3,  # Вес количества подписчиков
    'post_frequency': 0.3,  # Вес частоты постов
    'has_links': 0.2,  # Вес наличия ссылок
    'average_views': 0.2  # Вес среднего количества просмотров
}

# Веса для оценки релевантности поста
POST_RELEVANCE_WEIGHTS = {
    'time': 0.4,  # Вес актуальности по времени
    'channel': 0.3,  # Вес источника
    'views': 0.2,  # Вес просмотров
    'links': 0.1  # Вес наличия ссылок
}

# Веса для оценки лучшего поста в группе
BEST_POST_WEIGHTS = {
    'channel': 0.4,  # Вес канала
    'views': 0.4,  # Вес просмотров
    'links': 0.2  # Вес наличия ссылок
}

# Веса для определения рекламы
AD_WEIGHTS = {
    'keywords': 0.3,  # Вес ключевых слов
    'links': 0.2,  # Вес количества ссылок
    'patterns': 0.2,  # Вес паттернов
    'numbers': 0.15,  # Вес цифр
    'currency': 0.15  # Вес валютных символов
}

# Веса для определения экономической релевантности
ECONOMICS_WEIGHTS = {
    'экономика': 0.3,
    'финансы': 0.25,
    'банки': 0.2,
    'инвестиции': 0.15,
    'рынки': 0.1
}

# Нормализация значений
NORMALIZATION = {
    'subscribers': 1_000_000,  # Нормализация количества подписчиков
    'post_frequency': 20,  # Нормализация частоты постов
    'average_views': 100_000,  # Нормализация среднего количества просмотров
    'links_per_score': 5,  # Количество ссылок для максимального score
    'numbers_per_score': 10  # Количество цифр/валютных символов для максимального score
}

# Инициализация клиента Mistral если есть ключи
api_keys = os.getenv('MISTRAL_API_KEYS')
client = None
model = "mistral-embed"

if api_keys:
    try:
        api_keys = json.loads(api_keys)
        client = Mistral(api_key=api_keys[0])  # Используем первый ключ
    except Exception as e:
        logger.error(f"Ошибка инициализации Mistral API: {e}")

# Создаем директорию для сохранения данных
data_dir = Path("data")
data_dir.mkdir(exist_ok=True)

class NewsAggregator:
    def __init__(self):
        # Изменяем хранение источников: теперь хранится словарь user_id -> sources
        self.sources = {}
        self.news_cache = pd.DataFrame(columns=['source', 'text', 'date', 'url'])
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:90.0) Gecko/20100101 Firefox/90.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 11_5_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 Safari/605.1.15',
        ]
        try:
            # Проверяем наличие MONGODB_URI в переменных окружения
            if os.environ.get("MONGODB_URI"):
                print(f"Обнаружена переменная окружения MONGODB_URI: {os.environ.get('MONGODB_URI')[:20]}...")
            else:
                print("Переменная окружения MONGODB_URI не найдена!")
                
            # Инициализируем менеджер базы данных
            self.db_manager = MongoDBManager()
            print("MongoDB успешно инициализирована")
            
            # Загружаем начальные источники для удобства тестирования
            if not os.path.exists("sources.json"):
                # Создаем файл с базовыми источниками
                default_sources = [
                    {"name": "Банк России", "url": "https://t.me/centralbank_russia"},
                    {"name": "Мультипликатор", "url": "https://t.me/multievan"},
                    {"name": "Простая экономика", "url": "https://t.me/prostoecon"}
                ]
                with open("sources.json", "w", encoding="utf-8") as f:
                    json.dump(default_sources, f, ensure_ascii=False, indent=2)
                print(f"Создан файл sources.json с {len(default_sources)} базовыми источниками")
            
        except Exception as e:
            print(f"Ошибка при инициализации базы данных: {e}")
            # Если не удалось подключиться к MongoDB, используем пустой словарь источников
            self.db_manager = None
            print("Используем пустой набор источников")
        
    def parse_number(self, text):
        """Парсит число из текста, обрабатывая суффиксы K и M"""
        if not text:
            return 0
            
        # Удаляем все нецифровые символы, кроме точки, K и M
        text = text.strip().lower()
        number = ''
        decimal = ''
        has_decimal = False
        
        for char in text:
            if char.isdigit():
                if has_decimal:
                    decimal += char
                else:
                    number += char
            elif char == '.' and not has_decimal:
                has_decimal = True
            elif char == 'k':
                return int(float(number + '.' + decimal if decimal else number) * 1_000)
            elif char == 'm':
                return int(float(number + '.' + decimal if decimal else number) * 1_000_000)
        
        return int(number) if number else 0
    
    def extract_post_data(self, post, channel_name):
        """Извлекает данные из поста"""
        try:
            print(f"Начинаю извлечение данных из поста канала {channel_name}")
            
            # Получаем текст поста
            text_elem = post.find('div', {'class': 'tgme_widget_message_text'})
            if text_elem:
                text = text_elem.get_text()
                print(f"Найден текст поста длиной {len(text)} символов")
            else:
                text = ""
                print(f"ВНИМАНИЕ: Текстовый элемент не найден в посте канала {channel_name}")
            
            # Получаем дату
            date_elem = post.find('time')
            date = None
            if date_elem and date_elem.get('datetime'):
                date_str = date_elem['datetime']
                print(f"Найдена дата в посте: {date_str}")
                try:
                    date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    print(f"Преобразованная дата: {date}")
                except ValueError as e:
                    print(f"Ошибка преобразования даты '{date_str}': {e}")
            else:
                print(f"ВНИМАНИЕ: Элемент даты не найден в посте канала {channel_name}")
            
            # Получаем просмотры
            views_elem = post.find('span', {'class': 'tgme_widget_message_views'})
            if views_elem:
                views_text = views_elem.text.strip()
                print(f"Найден элемент просмотров: '{views_text}'")
                views = self.parse_number(views_text)
                print(f"Преобразованное количество просмотров: {views}")
            else:
                views = 0
                print(f"ВНИМАНИЕ: Элемент просмотров не найден в посте канала {channel_name}")
            
            # Получаем ссылки
            links = []
            seen_links = set()  # Для отслеживания дубликатов
            for link in post.find_all('a'):
                href = link.get('href')
                # Проверяем, что ссылка начинается с http:// или https://
                if href and (href.startswith('http://') or href.startswith('https://')):
                    # Фильтруем ссылки на сам канал и дубликаты
                    if href not in seen_links and not href.endswith(f"/{channel_name}") and not href.endswith(f"/{channel_name}/"):
                        links.append(href)
                        seen_links.add(href)
            print(f"Найдено {len(links)} уникальных ссылок в посте")
            
            # Получаем ID поста и ссылку на пост
            post_link = post.find('a', {'class': 'tgme_widget_message_date'})
            post_id = None
            post_url = None
            if post_link and post_link.get('href'):
                post_url = post_link['href']
                post_id = post_url.split('/')[-1]
                print(f"Найдена ссылка на пост: {post_url}, ID: {post_id}")
            else:
                print(f"ВНИМАНИЕ: Элемент ссылки на пост не найден в посте канала {channel_name}")
            
            # Получаем изображения
            images = []
            # Исключаем аватар канала и фото пользователей
            excluded_classes = ['tgme_widget_message_author_photo', 'tgme_widget_message_user_photo']
            
            # Ищем изображения в тегах tgme_widget_message_photo_wrap
            img_wraps = post.find_all('a', {'class': 'tgme_widget_message_photo_wrap'})
            print(f"Найдено {len(img_wraps)} элементов photo_wrap")
            
            for img_wrap in img_wraps:
                # Извлекаем URL изображения из атрибута style
                style = img_wrap.get('style', '')
                if 'background-image:url(' in style:
                    # Извлекаем URL из строки background-image:url('...')
                    try:
                        img_url = style.split("background-image:url('")[1].split("')")[0]
                        images.append(img_url)
                        print(f"Найдено изображение в photo_wrap: {img_url[:50]}...")
                    except Exception as e:
                        print(f"Ошибка при извлечении URL изображения из стиля '{style}': {e}")
            
            # Также ищем обычные изображения
            all_imgs = post.find_all(['img', 'a'])
            print(f"Найдено {len(all_imgs)} элементов img и a")
            
            for img in all_imgs:
                try:
                    # Проверяем, не находится ли изображение внутри тега i с классом tgme_page_photo_image или tgme_widget_message_user_photo
                    parent_i = img.find_parent('i', {'class': ['tgme_page_photo_image', 'tgme_widget_message_user_photo']})
                    if parent_i:
                        continue
                        
                    # Проверяем тег img
                    if img.name == 'img' and img.get('src'):
                        # Исключаем аватар канала и фото пользователей
                        if not any(cls in img.get('class', []) for cls in excluded_classes):
                            images.append(img['src'])
                            print(f"Найдено изображение в теге img: {img['src'][:50]}...")
                    # Проверяем ссылки на изображения
                    elif img.name == 'a' and img.get('href'):
                        href = img['href']
                        # Исключаем ссылки на аватар канала и фото пользователей
                        if not any(cls in img.get('class', []) for cls in excluded_classes) and any(href.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif']):
                            images.append(href)
                            print(f"Найдено изображение в теге a: {href[:50]}...")
                except Exception as e:
                    print(f"Ошибка при обработке элемента изображения: {e}")
            
            print(f"Всего найдено {len(images)} изображений в посте")
            
            result = {
                "channel": channel_name,
                "post_id": post_id,
                "post_url": post_url,
                "text": text,
                "date": date,
                "views": views,
                "links": links,
                "images": images, 
                "images_base64": []
            }
            
            print(f"Успешно извлечены данные из поста канала {channel_name}")
            return result
            
        except Exception as e:
            print(f"КРИТИЧЕСКАЯ ОШИБКА при извлечении данных из поста канала {channel_name}: {e}")
            # Предоставляем обратную совместимость, возвращая пустой словарь с базовыми полями
            return {
                "channel": channel_name,
                "post_id": None,
                "post_url": None,
                "text": "",
                "date": None,
                "views": 0,
                "links": [],
                "images": [], 
                "images_base64": []
            }
    
    def add_source(self, channel_username: str, user_id: int, name: str = None) -> bool:
        """Добавление нового источника новостей для конкретного пользователя"""
        try:
            if self.db_manager is not None:
                result = self.db_manager.add_source(channel_username, user_id, name)
                if result:
                    # Если источник успешно добавлен в БД, добавляем его в локальный набор
                    clean_username = channel_username
                    if clean_username.startswith('@'):
                        clean_username = clean_username[1:]
                    elif "t.me/s/" in clean_username:
                        clean_username = clean_username.split("t.me/s/")[-1]
                    elif "t.me/" in clean_username:
                        clean_username = clean_username.split("t.me/")[-1]
                    
                    # Инициализируем список источников для пользователя, если он еще не существует
                    if user_id not in self.sources:
                        self.sources[user_id] = set()
                    
                    self.sources[user_id].add(clean_username)
                return result
            else:
                # Если нет подключения к БД, добавляем только в локальный набор
                clean_username = channel_username
                if clean_username.startswith('@'):
                    clean_username = clean_username[1:]
                elif "t.me/s/" in clean_username:
                    clean_username = clean_username.split("t.me/s/")[-1]
                elif "t.me/" in clean_username:
                    clean_username = clean_username.split("t.me/")[-1]
                
                # Инициализируем список источников для пользователя, если он еще не существует
                if user_id not in self.sources:
                    self.sources[user_id] = set()
                
                if clean_username in self.sources[user_id]:
                    return False
                
                self.sources[user_id].add(clean_username)
                return True
        except Exception as e:
            print(f"Ошибка при добавлении источника: {e}")
            return False
            
    async def add_source_async(self, channel_username: str, user_id: int, name: str = None) -> bool:
        """Асинхронное добавление нового источника новостей для конкретного пользователя"""
        try:
            if self.db_manager is not None:
                result = await self.db_manager.add_source_async(channel_username, user_id, name)
                if result:
                    # Если источник успешно добавлен в БД, добавляем его в локальный набор
                    clean_username = channel_username
                    if clean_username.startswith('@'):
                        clean_username = clean_username[1:]
                    elif "t.me/s/" in clean_username:
                        clean_username = clean_username.split("t.me/s/")[-1]
                    elif "t.me/" in clean_username:
                        clean_username = clean_username.split("t.me/")[-1]
                    
                    # Инициализируем список источников для пользователя, если он еще не существует
                    if user_id not in self.sources:
                        self.sources[user_id] = set()
                    
                    self.sources[user_id].add(clean_username)
                return result
            else:
                # Если нет подключения к БД, добавляем только в локальный набор
                clean_username = channel_username
                if clean_username.startswith('@'):
                    clean_username = clean_username[1:]
                elif "t.me/s/" in clean_username:
                    clean_username = clean_username.split("t.me/s/")[-1]
                elif "t.me/" in clean_username:
                    clean_username = clean_username.split("t.me/")[-1]
                
                # Инициализируем список источников для пользователя, если он еще не существует
                if user_id not in self.sources:
                    self.sources[user_id] = set()
                
                if clean_username in self.sources[user_id]:
                    return False
                
                self.sources[user_id].add(clean_username)
                return True
        except Exception as e:
            print(f"Ошибка при асинхронном добавлении источника: {e}")
            return False
            
    def remove_source(self, channel_username: str, user_id: int) -> bool:
        """Удаление источника новостей для конкретного пользователя"""
        try:
            if self.db_manager is not None:
                result = self.db_manager.remove_source(channel_username, user_id)
                if result:
                    # Если источник успешно удален из БД, удаляем его из локального набора
                    clean_username = channel_username
                    if clean_username.startswith('@'):
                        clean_username = clean_username[1:]
                    elif "t.me/s/" in clean_username:
                        clean_username = clean_username.split("t.me/s/")[-1]
                    elif "t.me/" in clean_username:
                        clean_username = clean_username.split("t.me/")[-1]
                    
                    if user_id in self.sources and clean_username in self.sources[user_id]:
                        self.sources[user_id].remove(clean_username)
                return result
            else:
                # Если нет подключения к БД, удаляем только из локального набора
                clean_username = channel_username
                if clean_username.startswith('@'):
                    clean_username = clean_username[1:]
                elif "t.me/s/" in clean_username:
                    clean_username = clean_username.split("t.me/s/")[-1]
                elif "t.me/" in clean_username:
                    clean_username = clean_username.split("t.me/")[-1]
                
                if user_id not in self.sources or clean_username not in self.sources[user_id]:
                    return False
                
                self.sources[user_id].remove(clean_username)
                return True
        except Exception as e:
            print(f"Ошибка при удалении источника: {e}")
            return False
    
    async def remove_source_async(self, channel_username: str, user_id: int) -> bool:
        """Асинхронное удаление источника новостей для конкретного пользователя"""
        try:
            if self.db_manager is not None:
                result = await self.db_manager.remove_source_async(channel_username, user_id)
                if result:
                    # Если источник успешно удален из БД, удаляем его из локального набора
                    clean_username = channel_username
                    if clean_username.startswith('@'):
                        clean_username = clean_username[1:]
                    elif "t.me/s/" in clean_username:
                        clean_username = clean_username.split("t.me/s/")[-1]
                    elif "t.me/" in clean_username:
                        clean_username = clean_username.split("t.me/")[-1]
                    
                    if user_id in self.sources and clean_username in self.sources[user_id]:
                        self.sources[user_id].remove(clean_username)
                return result
            else:
                # Если нет подключения к БД, удаляем только из локального набора
                clean_username = channel_username
                if clean_username.startswith('@'):
                    clean_username = clean_username[1:]
                elif "t.me/s/" in clean_username:
                    clean_username = clean_username.split("t.me/s/")[-1]
                elif "t.me/" in clean_username:
                    clean_username = clean_username.split("t.me/")[-1]
                
                if user_id not in self.sources or clean_username not in self.sources[user_id]:
                    return False
                
                self.sources[user_id].remove(clean_username)
                return True
        except Exception as e:
            print(f"Ошибка при асинхронном удалении источника: {e}")
            return False
            
    def _load_sources_for_user(self, user_id: int) -> bool:
        """Загрузка источников из базы данных для конкретного пользователя"""
        try:
            if self.db_manager is not None:
                # Получаем список имен пользователей источников
                usernames = self.db_manager.get_source_usernames(user_id)
                self.sources[user_id] = set(usernames)
                return True
            return False
        except Exception as e:
            print(f"Ошибка при загрузке источников из БД для пользователя {user_id}: {e}")
            return False
    
    async def _load_sources_for_user_async(self, user_id: int) -> bool:
        """Асинхронная загрузка источников из базы данных для конкретного пользователя"""
        try:
            print(f"Начинаем загрузку источников для пользователя {user_id}")
            if self.db_manager is not None:
                print(f"Есть подключение к БД, получаем источники для пользователя {user_id}")
                # Получаем список имен пользователей источников
                usernames = await self.db_manager.get_source_usernames_async(user_id)
                print(f"Получено {len(usernames)} источников для пользователя {user_id}: {usernames}")
                self.sources[user_id] = set(usernames)
                return True
            print(f"Нет подключения к БД для пользователя {user_id}")
            return False
        except Exception as e:
            print(f"Ошибка при асинхронной загрузке источников из БД для пользователя {user_id}: {e}")
            return False
    
    def get_sources(self, user_id: int) -> Set[str]:
        """Получение списка источников для конкретного пользователя"""
        # Если источников для пользователя нет в кэше, загружаем их из БД
        if user_id not in self.sources:
            self._load_sources_for_user(user_id)
            if user_id not in self.sources:  # Если после загрузки все еще нет, создаем пустой набор
                self.sources[user_id] = set()
                
        return self.sources[user_id]
    
    async def get_sources_async(self, user_id: int) -> Set[str]:
        """Асинхронное получение списка источников для конкретного пользователя"""
        # Если источников для пользователя нет в кэше, загружаем их из БД
        if user_id not in self.sources:
            await self._load_sources_for_user_async(user_id)
            if user_id not in self.sources:  # Если после загрузки все еще нет, создаем пустой набор
                self.sources[user_id] = set()
                
        return self.sources[user_id]
    
    def get_source_details(self, user_id: int) -> List[Dict]:
        """Получение детальной информации об источниках для конкретного пользователя"""
        try:
            if self.db_manager is not None:
                return self.db_manager.get_all_sources(user_id)
            else:
                # Если нет подключения к БД, создаем простой список из локального набора
                sources = self.get_sources(user_id)
                return [{"username": src, "name": src, "url": f"https://t.me/s/{src}"} for src in sources]
        except Exception as e:
            print(f"Ошибка при получении детальной информации об источниках: {e}")
            return []
    
    async def get_source_details_async(self, user_id: int) -> List[Dict]:
        """Асинхронное получение детальной информации об источниках для конкретного пользователя"""
        try:
            if self.db_manager is not None:
                return await self.db_manager.get_all_sources_async(user_id)
            else:
                # Если нет подключения к БД, создаем простой список из локального набора
                sources = await self.get_sources_async(user_id)
                return [{"username": src, "name": src, "url": f"https://t.me/s/{src}"} for src in sources]
        except Exception as e:
            print(f"Ошибка при получении асинхронной детальной информации об источниках: {e}")
            return []
    
    def load_sources_from_json(self, json_file: str, user_id: int) -> bool:
        """Загрузка источников из JSON-файла для конкретного пользователя"""
        try:
            # Проверяем, существует ли файл
            if not os.path.exists(json_file):
                print(f"Файл {json_file} не найден, пропускаем загрузку")
                return False
                
            with open(json_file, 'r', encoding='utf-8') as file:
                channels = json.load(file)
                
            # Инициализируем список источников для пользователя, если он еще не существует
            if user_id not in self.sources:
                self.sources[user_id] = set()
                
            for channel in channels:
                # Извлекаем username из URL
                url = channel['url']
                if url.startswith('https://t.me/s/'):
                    username = url.replace('https://t.me/s/', '')
                    self.sources[user_id].add(username)
                elif url.startswith('https://t.me/'):
                    username = url.replace('https://t.me/', '')
                    self.sources[user_id].add(username)
            
            # Если есть подключение к БД, импортируем источники из JSON в БД
            if self.db_manager is not None:
                self.db_manager.import_from_json(channels, user_id)
                    
            return True
        except Exception as e:
            print(f"Ошибка при загрузке источников из JSON: {e}")
            return False

    async def load_sources_from_json_async(self, json_file: str, user_id: int) -> bool:
        """Асинхронная загрузка источников из JSON-файла для конкретного пользователя"""
        try:
            # Проверяем, существует ли файл
            if not os.path.exists(json_file):
                print(f"Файл {json_file} не найден, пропускаем загрузку")
                return False
                
            with open(json_file, 'r', encoding='utf-8') as file:
                json_data = json.load(file)
            
            # Проверяем формат JSON данных
            print(f"Формат JSON данных: {type(json_data)}")
            
            # Инициализируем список источников для пользователя, если он еще не существует
            if user_id not in self.sources:
                self.sources[user_id] = set()
            
            # Обрабатываем разные форматы данных
            # Проверяем новый формат с ключом "users"
            if isinstance(json_data, dict) and "users" in json_data:
                # Получаем источники для указанного пользователя
                user_id_str = str(user_id)
                if user_id_str in json_data["users"]:
                    user_sources = json_data["users"][user_id_str].get("sources", [])
                    print(f"Найдены источники для пользователя {user_id} в users: {user_sources}")
                    
                    # Добавляем источники в набор
                    for source in user_sources:
                        self.sources[user_id].add(source)
                        print(f"Добавлен источник: {source}")
                else:
                    # Если пользователя нет, используем default_sources
                    default_sources = json_data.get("default_sources", [])
                    print(f"Пользователь {user_id} не найден, используем default_sources: {default_sources}")
                    
                    for source in default_sources:
                        self.sources[user_id].add(source)
                        print(f"Добавлен источник по умолчанию: {source}")
            
            # Старый формат - список объектов с url
            elif isinstance(json_data, list):
                print(f"Найден старый формат данных (список)")
                for channel in json_data:
                    if isinstance(channel, dict) and "url" in channel:
                        # Извлекаем username из URL
                        url = channel['url']
                        if url.startswith('https://t.me/s/'):
                            username = url.replace('https://t.me/s/', '')
                            self.sources[user_id].add(username)
                            print(f"Добавлен источник из URL: {username}")
                        elif url.startswith('https://t.me/'):
                            username = url.replace('https://t.me/', '')
                            self.sources[user_id].add(username)
                            print(f"Добавлен источник из URL: {username}")
            
            # Если есть подключение к БД, импортируем источники из JSON в БД
            if self.db_manager is not None:
                sources_list = []
                for source in self.sources[user_id]:
                    sources_list.append({
                        "name": source,
                        "url": f"https://t.me/{source}"
                    })
                await self.db_manager.import_from_json_async(sources_list, user_id)
                    
            return True
        except Exception as e:
            print(f"Ошибка при асинхронной загрузке источников из JSON: {e}")
            import traceback
            print(traceback.format_exc())
            return False
    
    def save_sources_to_json(self, json_file: str = None, user_id: int = None) -> bool:
        """Сохранение источников в JSON-файл (используется только как резервный механизм)"""
        # Если не указан файл, просто пропускаем операцию сохранения
        if json_file is None:
            return True
            
        try:
            # Если есть подключение к БД, получаем полную информацию из БД
            if self.db_manager is not None and user_id is not None:
                channels = self.db_manager.get_all_sources(user_id)
            else:
                # Иначе создаем список из локального набора
                channels = []
                sources = self.get_sources(user_id) if user_id is not None else set()
                for source in sources:
                    channels.append({
                        'name': source,
                        'url': f"https://t.me/s/{source}"
                    })
                
            # Сохраняем в JSON-файл
            with open(json_file, 'w', encoding='utf-8') as file:
                json.dump(channels, file, ensure_ascii=False, indent=2)
                
            return True
        except Exception as e:
            print(f"Ошибка при сохранении источников в JSON: {e}")
            return False
    
    async def save_sources_to_json_async(self, json_file: str = None, user_id: int = None) -> bool:
        """Асинхронное сохранение источников в JSON-файл"""
        # Если не указан файл, просто пропускаем операцию сохранения
        if json_file is None:
            return True
            
        try:
            # Если есть подключение к БД, получаем полную информацию из БД
            if self.db_manager is not None and user_id is not None:
                channels = await self.db_manager.get_all_sources_async(user_id)
            else:
                # Иначе создаем список из локального набора
                channels = []
                sources = await self.get_sources_async(user_id) if user_id is not None else set()
                for source in sources:
                    channels.append({
                        'name': source,
                        'url': f"https://t.me/s/{source}"
                    })
            
            # Сохраняем в JSON-файл
            # Запись в файл не является асинхронной, но мы оборачиваем ее в run_in_executor
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: json.dump(channels, open(json_file, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
            )
                
            return True
        except Exception as e:
            print(f"Ошибка при асинхронном сохранении источников в JSON: {e}")
            return False
    
    def convert_to_preview_url(self, url):
        """Преобразует обычный URL канала в URL для превью"""
        if '/s/' not in url:
            parts = url.split('/')
            channel_name = parts[-1]
            return f"https://t.me/s/{channel_name}"
        return url
            
    async def get_latest_news(self, hours: int = 24, user_id: int = None) -> List[Dict]:
        """Получение последних новостей из всех источников с помощью веб-скрапинга"""
        news_list = []
        # Создаем время с учетом часового пояса
        time_cutoff = datetime.now(pytz.UTC) - timedelta(hours=hours)
        
        # Убедиться, что источники загружены
        await self.ensure_sources_loaded(user_id)
        
        # Получаем список источников для конкретного пользователя или используем общий список
        sources = await self.get_sources_async(user_id) if user_id is not None else set()
        print(f"Получено источников для пользователя {user_id}: {len(sources)}")
        print(f"Список источников: {sources}")
        
        # Если список пуст после всех проверок, возвращаем пустой список новостей
        if not sources:
            print(f"Список источников все еще пуст для пользователя {user_id}, возвращаем пустой список новостей")
            return []
        
        async with aiohttp.ClientSession() as session:
            tasks = []
            for source in sources:
                task = asyncio.create_task(self._scrape_channel(session, source, time_cutoff))
                tasks.append(task)
                
            # Ожидаем завершения всех задач
            channel_news_lists = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Обрабатываем результаты, игнорируя исключения
            for result in channel_news_lists:
                if isinstance(result, list):
                    news_list.extend(result)
                elif isinstance(result, Exception):
                    print(f"Ошибка при скрапинге канала: {result}")
        
        return news_list
        
    async def _scrape_channel(self, session: aiohttp.ClientSession, channel: str, time_cutoff: datetime) -> List[Dict]:
        """Парсинг канала из веб-версии Telegram с использованием нового парсера"""
        channel_news = []
        url = f"https://t.me/s/{channel}"
        
        print(f"[DEBUG] Начинаю скрапинг канала {channel}, URL: {url}")
        start_time = time.time()
        
        try:
            headers = {
                'User-Agent': random.choice(self.user_agents),
                'Accept': 'text/html,application/xhtml+xml,application/xml',
                'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
            
            print(f"[DEBUG] Отправляю запрос к каналу {channel} с User-Agent: {headers['User-Agent']}")
            request_start_time = time.time()
            
            async with session.get(url, headers=headers, timeout=30) as response:
                request_time = time.time() - request_start_time
                print(f"[DEBUG] Получен ответ от канала {channel}, статус: {response.status}, время запроса: {request_time:.2f} сек.")
                
                if response.status != 200:
                    print(f"[ERROR] Ошибка при запросе канала {channel}: HTTP {response.status}")
                    print(f"[ERROR] Заголовки ответа: {response.headers}")
                    return []
                    
                html_start_time = time.time()
                html = await response.text()
                html_time = time.time() - html_start_time
                print(f"[DEBUG] Получен HTML для канала {channel}, размер: {len(html)} байт, время получения: {html_time:.2f} сек.")
                
                # Проверяем наличие контента
                if len(html) < 100:
                    print(f"[WARNING] Слишком короткий HTML для канала {channel}: {html[:100]}")
                    return []
                
                parse_start_time = time.time()
                soup = BeautifulSoup(html, 'html.parser')
                parse_time = time.time() - parse_start_time
                print(f"[DEBUG] Время парсинга HTML: {parse_time:.2f} сек.")
                
                # Находим все сообщения канала
                posts_search_start = time.time()
                posts = soup.find_all('div', {'class': 'tgme_widget_message'})
                posts_search_time = time.time() - posts_search_start
                print(f"[DEBUG] Найдено {len(posts)} сообщений для канала {channel}, время поиска: {posts_search_time:.2f} сек.")
                
                if not posts:
                    print(f"[WARNING] Не найдены сообщения для канала {channel}")
                    # Проверяем наличие страницы канала вообще
                    channel_info = soup.find('div', {'class': 'tgme_page_additional'})
                    if channel_info:
                        print(f"[INFO] Информация о канале {channel} найдена: {channel_info.text}")
                    else:
                        print(f"[ERROR] Информация о канале {channel} не найдена, возможно неверное имя канала или блокировка доступа")
                        
                    # Сохраняем HTML для отладки
                    debug_path = f"debug_html_{channel}_{int(time.time())}.html"
                    try:
                        with open(debug_path, 'w', encoding='utf-8') as f:
                            f.write(html)
                        print(f"[DEBUG] Сохранен отладочный HTML в файл: {debug_path}")
                    except Exception as e:
                        print(f"[ERROR] Не удалось сохранить отладочный HTML: {e}")
                    return []
                
                # Анализируем найденные посты
                print(f"[DEBUG] Начинаю обработку {len(posts)} постов из канала {channel}")
                for post_index, post in enumerate(posts):
                    try:
                        post_start_time = time.time()
                        print(f"[DEBUG] Обработка поста #{post_index+1}/{len(posts)} из канала {channel}")
                        
                        # Получаем ID поста для отладки
                        post_id = post.get('data-post-id', 'unknown')
                        print(f"[DEBUG] ID поста #{post_index+1}: {post_id}")
                        
                        # Извлекаем данные поста
                        extract_start_time = time.time()
                        post_data = self.extract_post_data(post, channel)
                        extract_time = time.time() - extract_start_time
                        print(f"[DEBUG] Время извлечения данных поста #{post_index+1}: {extract_time:.2f} сек.")
                        
                        # Отладочная информация о полученных данных
                        text_length = len(post_data['text']) if post_data['text'] else 0
                        print(f"[DEBUG] Результаты извлечения для поста #{post_index+1}:")
                        print(f"  - Текст: {text_length} символов")
                        print(f"  - Дата: {post_data['date']}")
                        print(f"  - Просмотры: {post_data['views']}")
                        print(f"  - Ссылки: {len(post_data['links']) if 'links' in post_data else 0}")
                        print(f"  - Изображения: {len(post_data['images']) if 'images' in post_data else 0}")
                        
                        # Проверяем наличие текста
                        if not post_data['text']:
                            print(f"[WARNING] Пост #{post_index+1} не содержит текста, пропускаем")
                            continue
                        
                        # Проверяем дату
                        if not post_data['date']:
                            print(f"[WARNING] Пост #{post_index+1} не содержит даты, пропускаем")
                            continue
                            
                        # Обеспечиваем, что дата поста имеет timezone
                        post_date = post_data['date']
                        if post_date.tzinfo is None:
                            post_date = pytz.UTC.localize(post_date)
                            print(f"[DEBUG] Добавлен часовой пояс UTC к дате поста #{post_index+1}")
                            
                        # Сравниваем даты с учетом часовых поясов
                        time_diff = post_date - time_cutoff
                        hours_diff = time_diff.total_seconds() / 3600
                        print(f"[DEBUG] Пост #{post_index+1} от {post_date}, разница со временем отсечения: {hours_diff:.2f} часов")
                        
                        if post_date < time_cutoff:
                            print(f"[INFO] Пост #{post_index+1} слишком старый (до {time_cutoff}), пропускаем")
                            continue
                            
                        # Добавляем пост в список новостей
                        news_item = {
                            'source': channel,
                            'text': post_data['text'],
                            'date': post_date,
                            'url': post_data['post_url'],
                            'views': post_data['views'],
                            'links': post_data.get('links', []),
                            'images': post_data.get('images', [])
                        }
                        channel_news.append(news_item)
                        print(f"[SUCCESS] Пост #{post_index+1} успешно добавлен в список новостей")
                        
                        post_time = time.time() - post_start_time
                        print(f"[DEBUG] Общее время обработки поста #{post_index+1}: {post_time:.2f} сек.")
                        
                    except Exception as e:
                        error_info = traceback.format_exc()
                        print(f"[ERROR] Ошибка при обработке поста #{post_index+1} из канала {channel}:")
                        print(f"{error_info}")
                        continue
                
                total_time = time.time() - start_time
                print(f"[DEBUG] Обработка канала {channel} завершена, получено {len(channel_news)} новостей, общее время: {total_time:.2f} сек.")
                        
        except aiohttp.ClientError as e:
            print(f"[ERROR] Ошибка клиента при подключении к каналу {channel}: {e}")
            error_details = traceback.format_exc()
            print(f"[ERROR] Детали ошибки: {error_details}")
            
        except asyncio.TimeoutError:
            print(f"[ERROR] Превышено время ожидания при подключении к каналу {channel}")
            
        except Exception as e:
            print(f"[ERROR] Неизвестная ошибка при парсинге канала {channel}: {e}")
            error_details = traceback.format_exc()
            print(f"[ERROR] Детали ошибки: {error_details}")
            
        # Возвращаем результаты
        print(f"[INFO] Канал {channel} вернул {len(channel_news)} новостей")
        return channel_news
        
    def remove_duplicates(self, news_list: List[Dict]) -> List[Dict]:
        """Улучшенное удаление дубликатов новостей с использованием сравнения схожести текстов"""
        if not news_list:
            return []
            
        # Преобразуем в DataFrame для базовой фильтрации дубликатов
        df = pd.DataFrame(news_list)
        
        # Сначала удаляем абсолютные дубликаты (полное совпадение текста)
        if 'text' in df.columns:
            df = df.drop_duplicates(subset=['text'], keep='first')
        
        # Преобразуем обратно в список словарей для более сложной фильтрации
        filtered_news = df.to_dict('records')
        
        # Используем более сложный алгоритм для выявления похожих новостей
        result_news = []
        for news in filtered_news:
            is_duplicate = False
            news_text = news['text'].lower()
            
            # Удаляем пунктуацию и лишние пробелы для лучшего сравнения
            news_text = re.sub(r'[^\w\s]', '', news_text)
            news_text = re.sub(r'\s+', ' ', news_text).strip()
            
            for added_news in result_news:
                added_text = added_news['text'].lower()
                added_text = re.sub(r'[^\w\s]', '', added_text)
                added_text = re.sub(r'\s+', ' ', added_text).strip()
                
                # Если тексты очень короткие (менее 30 символов), требуем полного совпадения
                if len(news_text) < 30 and news_text == added_text:
                    is_duplicate = True
                    break
                
                # Для более длинных текстов: рассчитываем схожесть
                if len(news_text) >= 30:
                    # Проверяем общие слова и фразы
                    news_words = set(news_text.split())
                    added_words = set(added_text.split())
                    
                    common_words = news_words.intersection(added_words)
                    
                    # Если более 70% слов совпадают, считаем дубликатом
                    threshold = 0.7  # Порог схожести
                    if len(common_words) / max(len(news_words), len(added_words)) > threshold:
                        is_duplicate = True
                        break
                        
                    # Проверяем содержание одного текста в другом (для коротких/длинных вариантов одной новости)
                    if len(news_text) < len(added_text) and news_text in added_text:
                        is_duplicate = True
                        break
                        
                    if len(added_text) < len(news_text) and added_text in news_text:
                        is_duplicate = True
                        # Заменяем уже добавленную новость на более полную версию
                        added_news.update(news)
                        break
            
            if not is_duplicate:
                result_news.append(news)
        
        return result_news
        
    def rank_news(self, news_list: List[Dict]) -> List[Dict]:
        """Ранжирование новостей по важности (с учетом просмотров, если доступны)"""
        if not news_list:
            return []
        
        # Убедимся, что у всех дат есть часовой пояс для корректного сравнения
        for news in news_list:
            if 'date' in news and news['date'].tzinfo is None:
                news['date'] = pytz.UTC.localize(news['date'])
            
        # Расширенное ранжирование с учетом просмотров
        for news in news_list:
            # Добавляем оценку новости
            news['score'] = 1.0  # Базовая оценка
            
            # Учитываем количество просмотров, если доступны
            if 'views' in news and news['views']:
                # Нормализуем просмотры: более 10K просмотров дают максимальный бонус
                views_factor = min(news['views'] / 10000, 1.0)
                news['score'] += views_factor * 2  # Максимум +2 за просмотры
            
            # Учитываем свежесть новости (более новые имеют приоритет)
            if 'date' in news:
                # Убедимся, что текущее время тоже имеет часовой пояс
                now = datetime.now(pytz.UTC)
                hours_ago = (now - news['date']).total_seconds() / 3600
                recency_factor = max(1.0 - (hours_ago / 24), 0)  # От 0 до 1, где 1 - самые свежие
                news['score'] += recency_factor * 1.5  # Максимум +1.5 за свежесть
        
        # Сортируем по итоговой оценке
        ranked_news = sorted(news_list, key=lambda x: x.get('score', 0), reverse=True)
        
        # Удаляем временное поле score
        for news in ranked_news:
            if 'score' in news:
                del news['score']
                
        return ranked_news

    async def download_image(self, session, url):
        """Скачивает изображение и конвертирует его в base64"""
        try:
            headers = {
                'User-Agent': random.choice(self.user_agents)
            }
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    image_data = await response.read()
                    # Конвертируем в base64
                    base64_image = base64.b64encode(image_data).decode('utf-8')
                    return base64_image
        except Exception as e:
            logger.error(f"Ошибка при скачивании изображения {url}: {e}")
        return None 

    def estimate_source_weight(self, channel_info):
        """Оценка источника (автоматически по метаданным)"""
        subs_score = min(channel_info['subscribers'] / NORMALIZATION['subscribers'], 1.0)
        freq_score = min(channel_info['post_frequency_per_day'] / NORMALIZATION['post_frequency'], 1.0)
        link_score = channel_info['has_links_ratio']
        views_score = min(channel_info['average_views'] / NORMALIZATION['average_views'], 1.0)
        return round(
            SOURCE_WEIGHTS['subscribers'] * subs_score + 
            SOURCE_WEIGHTS['post_frequency'] * freq_score + 
            SOURCE_WEIGHTS['has_links'] * link_score + 
            SOURCE_WEIGHTS['average_views'] * views_score, 
            3
        )

    def calculate_post_relevance(self, post, channel_weight, max_views):
        """Рассчитывает релевантность поста на основе нескольких факторов"""
        try:
            # Оценка актуальности по времени
            post_date = post["date"]
            if isinstance(post_date, str):
                post_date = datetime.fromisoformat(post_date.replace('Z', '+00:00'))
            
            # Обеспечиваем, что дата имеет часовой пояс
            if post_date.tzinfo is None:
                post_date = pytz.UTC.localize(post_date)
                
            now = datetime.now(pytz.UTC)
            time_diff = now - post_date
            time_score = max(0, 1 - (time_diff.total_seconds() / (24 * 3600)))  # 1.0 -> 0.0 за 24 часа
            
            # Оценка по просмотрам (нормализованная)
            views_score = post["views"] / max_views if max_views > 0 else 0
            
            # Оценка по наличию внешних ссылок
            links_score = min(len(post["links"]) * (1.0 / NORMALIZATION['links_per_score']), 1.0)  # Максимум 1.0 за 5+ ссылок
            
            # Итоговая оценка (можно настроить веса)
            relevance = (
                POST_RELEVANCE_WEIGHTS['time'] * time_score +      # Актуальность
                POST_RELEVANCE_WEIGHTS['channel'] * channel_weight +  # Вес источника
                POST_RELEVANCE_WEIGHTS['views'] * views_score +     # Популярность
                POST_RELEVANCE_WEIGHTS['links'] * links_score      # Подтверждение информации
            )
            
            return relevance
        except Exception as e:
            logger.error(f"Ошибка при расчете релевантности поста: {e}")
            return 0

    def get_text_embedding(self, texts, batch_size=5):
        """Получение эмбеддингов через Mistral API с учетом ограничений"""
        if not client:
            logger.error("Mistral API не инициализирован")
            return None
            
        try:
            all_embeddings = []
            
            # Разбиваем тексты на батчи
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i+batch_size]
                
                # Получаем эмбеддинги для текущего батча
                embeddings_response = client.embeddings.create(
                    model=model,
                    inputs=batch
                )
                
                # Добавляем эмбеддинги из батча в общий список
                batch_embeddings = [data.embedding for data in embeddings_response.data]
                all_embeddings.extend(batch_embeddings)
                
                # Строго соблюдаем ограничение в 1 запрос в секунду
                if i + batch_size < len(texts):
                    time.sleep(2)  # Увеличиваем задержку до 2 секунд между запросами
            
            return all_embeddings
        except Exception as e:
            logger.error(f"Ошибка при получении эмбеддингов: {e}")
            return None

    def find_similar_posts(self, posts, threshold=SIMILARITY_THRESHOLD, batch_size=5):
        """Находит семантически похожие посты используя Mistral эмбеддинги"""
        # Получаем тексты постов
        texts = [post["text"] for post in posts]
        
        # Получаем эмбеддинги для всех текстов с батчингом
        embeddings = self.get_text_embedding(texts, batch_size=batch_size)
        if not embeddings:
            return []
        
        # Вычисляем попарную схожесть
        similarity_matrix = cosine_similarity(embeddings)
        
        # Группируем похожие посты
        similar_groups = []
        used_indices = set()
        
        for i in range(len(posts)):
            if i in used_indices:
                continue
                
            group = [i]
            used_indices.add(i)
            
            for j in range(i + 1, len(posts)):
                if j not in used_indices and similarity_matrix[i][j] > threshold:
                    group.append(j)
                    used_indices.add(j)
                    
            similar_groups.append(group)
        
        return similar_groups

    def select_best_post(self, group_indices, posts, channel_weights):
        """Выбирает лучший пост из группы похожих"""
        group_posts = [posts[i] for i in group_indices]
        
        # Рассчитываем вес для каждого поста
        post_scores = []
        for post in group_posts:
            channel_weight = channel_weights.get(post["channel"], 0)
            # Учитываем вес канала, количество просмотров и наличие ссылок
            score = (
                BEST_POST_WEIGHTS['channel'] * channel_weight +
                BEST_POST_WEIGHTS['views'] * (post["views"] / max(p["views"] for p in group_posts)) +
                BEST_POST_WEIGHTS['links'] * min(len(post["links"]) * (1.0 / NORMALIZATION['links_per_score']), 1.0)
            )
            post_scores.append(score)
        
        # Возвращаем пост с максимальным весом
        best_index = group_indices[post_scores.index(max(post_scores))]
        return posts[best_index]

    def calculate_cosine_similarity(self, text1, text2):
        """Вычисляет косинусное сходство между двумя текстами"""
        vectorizer = TfidfVectorizer()
        try:
            tfidf_matrix = vectorizer.fit_transform([text1, text2])
            return cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])[0][0]
        except:
            return 0.0

    def merge_similar_posts(self, posts, similarity_threshold=MERGE_SIMILARITY_THRESHOLD):
        """Объединяет похожие посты на основе косинусного сходства"""
        merged_posts = []
        used_indices = set()
        
        for i, post1 in enumerate(posts):
            if i in used_indices:
                continue
                
            current_group = [post1]
            used_indices.add(i)
            
            for j, post2 in enumerate(posts[i+1:], i+1):
                if j in used_indices:
                    continue
                    
                similarity = self.calculate_cosine_similarity(post1["text"], post2["text"])
                if similarity >= similarity_threshold:
                    current_group.append(post2)
                    used_indices.add(j)
            
            if len(current_group) > 1:
                # Объединяем посты из группы
                merged_post = self.merge_post_group(current_group)
                merged_posts.append(merged_post)
            else:
                merged_posts.append(post1)
        
        return merged_posts

    def is_advertisement(self, text, links):
        """Определяет, является ли пост рекламным"""
        # Ключевые слова и фразы, указывающие на рекламу
        ad_keywords = {
            'прямые_призывы': [
                'реклама', 'рекламный', 'спонсор', 'партнер', 'сотрудничество', 'коллаборация',
                'акция', 'скидка', 'специальное предложение', 'промокод', 'предложение дня',
                'купить', 'заказать', 'цена', 'стоимость', 'руб', '₽', 'скидочный',
                'инвестируй', 'инвестиции', 'брокер', 'трейдинг', 'торговля',
                'регистрация', 'бонус', 'приз', 'выигрыш', 'розыгрыш', 'конкурс',
                'подпишись', 'подписка', 'канал', 'каналы', 'telegram', 't.me/',
                't.me', 'telegram.me', 'telegram.org', 'сейчaс', 'сейчас',
                'эксклюзив', 'новинка', 'ультра', 'ограничено', 'лимитированное'
            ],
            'финансовые_термины': [
                'депозит', 'вклад', 'кредит', 'займ', 'микрозайм', 'финансирование',
                'процент', 'годовых', 'доходность', 'прибыль', 'дивиденды', 'акции',
                'облигации', 'фонд', 'портфель', 'инвестиционный', 'брокерский', 'счет',
                'карта', 'кэшбэк', 'бонусы', 'ликвидность', 'валюта', 'инфляция',
                'оборот', 'рентабельность', 'ROI'
            ],
            'маркетинговые_слова': [
                'эксклюзивно', 'только сейчас', 'ограниченное предложение', 'успей',
                'последний шанс', 'специальная цена', 'выгодно', 'бесплатно',
                'в подарок', 'при покупке', 'скидка', 'распродажа', 'новинка', 'хит продаж',
                'бестселлер', 'популярный', 'не пропусти', 'горячее предложение', 'ограниченное время',
                'топ предложение', 'выбор редакции', 'рекомендация эксперта'
            ],
            'призывы_к_действию': [
                'нажми', 'кликни', 'перейди', 'зарегистрируйся', 'подпишись',
                'оставь заявку', 'заполни форму', 'свяжитесь', 'позвони', 'напиши',
                'закажи', 'купи', 'получи', 'воспользуйся', 'присоединяйся', 'запишись',
                'узнай подробнее', 'детали', 'смотри', 'сегодня', 'не упусти шанс',
                'подробности', 'сделай заказ'
            ]
        }

        # Проверка на наличие рекламных ключевых слов
        text_lower = text.lower()
        keyword_scores = {}
        total_keyword_score = 0
        
        for category, keywords in ad_keywords.items():
            matches = sum(1 for keyword in keywords if keyword in text_lower)
            score = matches / len(keywords)
            keyword_scores[category] = score
            total_keyword_score += score
        
        # Проверка на наличие множества ссылок
        link_score = min(len(links) / NORMALIZATION['links_per_score'], 1.0)  # Нормализуем до 1.0
        
        # Проверка на наличие рекламных паттернов в тексте
        ad_patterns = [
            r'\b\d+\s*%\s*(?:скидк|скидка|off|discount)\b',
            r'\b(?:от|до)\s*\d+\s*(?:руб|₽|р\.)\b',
            r'\b(?:купи|закажи|получи)\b.*\b(?:бесплатно|в подарок)\b',
            r'\b(?:подпишись|подписка)\b.*\b(?:канал|каналы)\b',
            r'\b(?:инвестируй|вкладывай)\b.*\b(?:сейчас|сегодня)\b',
            r'\b(?:только|лишь)\b.*\b(?:до|по)\b.*\d{1,2}(?:\.\d{1,2})?',
            r'\b(?:акция|спецпредложение)\b.*\b(?:действует|действует до)\b',
            r'\b(?:получи|забери)\b.*\b(?:бонус|подарок)\b',
            r'\b(?:регистрация|заявка)\b.*\b(?:бесплатно|без оплаты)\b'
        ]
        
        pattern_matches = sum(1 for pattern in ad_patterns if re.search(pattern, text_lower, re.IGNORECASE))
        pattern_score = pattern_matches / len(ad_patterns)
        
        # Проверка на наличие множества цифр и валютных символов
        number_count = len(re.findall(r'\d+(?:\.\d+)?%?', text))
        currency_count = len(re.findall(r'[$€£₽₴]', text))
        number_score = min((number_count + currency_count) / NORMALIZATION['numbers_per_score'], 1.0)  # Нормализуем до 1.0
        
        # Вычисляем итоговый score с весами
        ad_score = (
            AD_WEIGHTS['keywords'] * (total_keyword_score / len(ad_keywords)) +  # Вес ключевых слов
            AD_WEIGHTS['links'] * link_score +                               # Вес количества ссылок
            AD_WEIGHTS['patterns'] * pattern_score +                            # Вес паттернов
            AD_WEIGHTS['numbers'] * number_score +                            # Вес цифр и валют
            AD_WEIGHTS['currency'] * max(keyword_scores.values())              # Вес наиболее выраженной категории
        )
        
        # Определяем, является ли пост рекламным
        is_ad = ad_score > AD_THRESHOLD or (
            # Дополнительные условия для определения рекламы
            (link_score > 0.8 and pattern_score > 0.3) or  # Много ссылок и паттернов
            (number_score > 0.8 and total_keyword_score > 0.3) or  # Много цифр и ключевых слов
            (max(keyword_scores.values()) > 0.7)  # Очень выраженная категория
        )
        
        return is_ad, ad_score

    def is_economics_related(self, text):
        """Проверяет релевантность поста экономической тематике используя Mistral"""
        if not client:
            logger.warning("Mistral API не инициализирован, нельзя проверить экономическую релевантность")
            return False, 0, {}
            
        # Эталонные тексты для каждой категории
        reference_texts = {
            'экономика': [
                "Экономический рост в стране замедлился до 1.5% в годовом выражении. Инфляция остается в целевых пределах.",
                "Макроэкономические показатели демонстрируют стабильность. ВВП растет, инфляция под контролем.",
                "Экономическая политика направлена на стимулирование роста и поддержание финансовой стабильности."
            ],
            'финансы': [
                "Финансовый рынок показал положительную динамику. Инвесторы проявляют повышенный интерес.",
                "Бюджетная политика остается консервативной. Налоговые поступления растут.",
                "Финансовая система демонстрирует устойчивость. Банковский сектор укрепляется."
            ],
            'банки': [
                "Банковский сектор показывает рост прибыли. Кредитный портфель расширяется.",
                "Центральный банк сохраняет ключевую ставку. Банковская система стабильна.",
                "Банки увеличивают объемы кредитования. Процентные ставки снижаются."
            ],
            'инвестиции': [
                "Инвестиционный климат улучшается. Прямые иностранные инвестиции растут.",
                "Инвесторы проявляют интерес к новым проектам. Инвестиционный портфель расширяется.",
                "Инвестиционная активность в регионе увеличивается. Новые проекты привлекают капитал."
            ],
            'рынки': [
                "Фондовый рынок достиг новых максимумов. Торговые объемы растут.",
                "Рынок облигаций демонстрирует стабильность. Доходности снижаются.",
                "Товарные рынки показывают разнонаправленную динамику. Волатильность снижается."
            ]
        }
        
        # Получаем эмбеддинги для входного текста
        text_embedding = self.get_text_embedding([text], batch_size=1)
        if not text_embedding:
            return False, 0, {}
        
        text_embedding = text_embedding[0]
        
        # Получаем эмбеддинги для эталонных текстов по категориям
        category_embeddings = {}
        for category, refs in reference_texts.items():
            # Обрабатываем эталонные тексты небольшими батчами
            ref_embeddings = self.get_text_embedding(refs, batch_size=2)
            if not ref_embeddings:
                continue
            category_embeddings[category] = ref_embeddings
        
        # Вычисляем схожесть с эталонными текстами
        scores = {}
        
        for category, ref_embeddings in category_embeddings.items():
            similarities = cosine_similarity([text_embedding], ref_embeddings)[0]
            scores[category] = max(similarities)
        
        # Вычисляем итоговый score
        total_score = sum(score * ECONOMICS_WEIGHTS[category] 
                         for category, score in scores.items())
        
        return total_score > ECONOMICS_RELEVANCE_THRESHOLD, total_score, scores

    def get_post_type(self, category_scores):
        """Определяет тип поста на основе scores категорий"""
        # Пороговые значения для определения типа
        THRESHOLD = 0.5
        
        # Проверяем, что category_scores не пустой
        if not category_scores:
            return "общий"
        
        # Сортируем категории по score
        sorted_categories = sorted(category_scores.items(), key=lambda x: x[1], reverse=True)
        
        # Если максимальный score выше порога, определяем тип
        if sorted_categories[0][1] > THRESHOLD:
            return sorted_categories[0][0]
        
        # Если есть несколько категорий с близкими scores
        top_categories = [cat for cat, score in sorted_categories if score > THRESHOLD * 0.8]
        if len(top_categories) > 1:
            return "смешанный"
        
        return "общий"

    def merge_post_group(self, posts):
        """Объединяет группу похожих постов в один пост"""
        # Смотрим, есть ли поле weight в постах
        has_weight = all("weight" in post for post in posts)
        
        if has_weight:
            # Берем пост с наибольшим весом как основной
            main_post = max(posts, key=lambda x: x["weight"])
        else:
            # Если нет весов, берем пост с наибольшим количеством просмотров
            main_post = max(posts, key=lambda x: x["views"])
        
        # Объединяем метаданные
        merged_post = main_post.copy()
        
        # Проверяем наличие текста
        if not merged_post.get("text", "").strip():
            return None  # Возвращаем None для постов без текста
        
        # Объединяем просмотры
        merged_post["views"] = sum(post["views"] for post in posts)
        
        # Объединяем ссылки
        all_links = set()
        for post in posts:
            all_links.update(post.get("links", []))
        merged_post["links"] = list(all_links)
        
        # Объединяем изображения если есть
        if "images" in merged_post:
            all_images = set()
            all_images_base64 = []
            for post in posts:
                all_images.update(post.get("images", []))
                all_images_base64.extend(post.get("images_base64", []))
            merged_post["images"] = list(all_images)
            merged_post["images_base64"] = all_images_base64
        
        # Проверяем на рекламу
        is_ad, ad_score = self.is_advertisement(merged_post["text"], merged_post["links"])
        merged_post["is_advertisement"] = is_ad
        merged_post["ad_score"] = round(ad_score, 3)
        
        # Проверяем на релевантность экономической тематике если доступно API
        if client:
            is_econ, econ_score, category_scores = self.is_economics_related(merged_post["text"])
            merged_post["is_economics_related"] = is_econ
            merged_post["economics_score"] = round(econ_score, 3)
            merged_post["category_scores"] = {k: round(v, 3) for k, v in category_scores.items()}
            merged_post["post_type"] = self.get_post_type(category_scores)
        
        # Добавляем информацию о слиянии
        merged_post["merged_from"] = len(posts)
        merged_post["original_posts"] = [
            {
                "channel": post.get("channel", post.get("source", "")),
                "date": post["date"],
                "views": post["views"],
                "post_url": post.get("post_url", post.get("url", "")),
                "is_advertisement": self.is_advertisement(post.get("text", ""), post.get("links", []))[0] if post.get("text", "").strip() else False
            } for post in posts
        ]
        
        return merged_post

    def get_next_api_key(self):
        """Получает следующий API ключ из списка"""
        if not client or not api_keys:
            return None
            
        current_key = client.api_key
        current_index = api_keys.index(current_key)
        next_index = (current_index + 1) % len(api_keys)
        return api_keys[next_index]

    def handle_api_error(self, func):
        """Декоратор для обработки ошибок API"""
        def wrapper(*args, **kwargs):
            if not client or not api_keys:
                return None
                
            max_retries = len(api_keys)
            for _ in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    logger.error(f"Ошибка API: {e}")
                    client.api_key = self.get_next_api_key()
            return None
        return wrapper

    async def analyze_and_sort_posts(self, news_list, channel_weights=None):
        """Анализирует, удаляет дубликаты и сортирует посты по достоверности"""
        try:
            if not news_list:
                return []
                
            # Инициализируем веса каналов если не переданы
            if channel_weights is None:
                channel_weights = {}
                # Устанавливаем дефолтный вес в 0.5 для всех каналов
                for post in news_list:
                    channel = post.get("channel", post.get("source", ""))
                    if channel not in channel_weights:
                        channel_weights[channel] = 0.5
            
            # Находим группы похожих постов если доступно API
            if client:
                similar_groups = self.find_similar_posts(news_list)
                
                # Выбираем лучшие посты из каждой группы
                unique_posts = []
                for group in similar_groups:
                    best_post = self.select_best_post(group, news_list, channel_weights)
                    unique_posts.append(best_post)
            else:
                # Если API недоступно, используем базовое удаление дубликатов
                unique_posts = self.remove_duplicates(news_list)
            
            # Рассчитываем итоговый вес для каждого уникального поста
            posts_with_weight = []
            max_views = max(post["views"] for post in unique_posts) if unique_posts else 1
            
            for post in unique_posts:
                channel = post.get("channel", post.get("source", ""))
                weight = self.calculate_post_relevance(
                    post,
                    channel_weights.get(channel, 0.5),
                    max_views
                )
                post["weight"] = weight
                posts_with_weight.append((post, weight))
            
            # Сортируем посты по весу
            sorted_posts = sorted(posts_with_weight, key=lambda x: x[1], reverse=True)
            
            # Подготавливаем для возврата посты с весами
            result_posts = [post for post, _ in sorted_posts]
            
            return result_posts
            
        except Exception as e:
            logger.error(f"Ошибка при анализе постов: {e}")
            return news_list 

    async def get_channel_metadata_web(self, channel_url, channel_name, all_posts_data, posts_count=POSTS_TO_ANALYZE, days_to_analyze=2):
        """Получение метаданных канала через веб-скрапинг"""
        try:
            preview_url = self.convert_to_preview_url(channel_url)
            headers = {
                'User-Agent': random.choice(self.user_agents)
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(preview_url, headers=headers) as response:
                    if response.status == 200:
                        html = await response.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        
                        # Извлекаем количество подписчиков
                        subscribers = 0
                        subscribers_text = soup.find('div', {'class': 'tgme_header_counter'})
                        if subscribers_text:
                            # Ищем число подписчиков в тексте
                            match = re.search(r'(\d+(?:\.\d+)?[KkMm]?)\s*(?:subscribers|подписчиков)', subscribers_text.text)
                            if match:
                                subscribers = self.parse_number(match.group(1))
                        
                        # Анализируем последние посты
                        posts = soup.find_all('div', {'class': 'tgme_widget_message'})
                        
                        now = datetime.now(pytz.UTC)
                        day_ago = now - timedelta(days=days_to_analyze)
                        
                        # Подсчитываем количество постов за указанный период
                        recent_posts = []
                        posts_with_links = 0
                        total_views = 0
                        
                        for post in posts[:posts_count]:  # Используем переданное количество постов
                            # Проверяем наличие ссылок
                            links = post.find_all('a')
                            if links:
                                posts_with_links += 1
                            
                            # Подсчитываем просмотры
                            views_elem = post.find('span', {'class': 'tgme_widget_message_views'})
                            if views_elem:
                                views = self.parse_number(views_elem.text.strip())
                                total_views += views
                            
                            # Проверяем дату поста
                            date_elem = post.find('time')
                            if date_elem and date_elem.get('datetime'):
                                post_date = datetime.fromisoformat(date_elem['datetime'].replace('Z', '+00:00'))
                                if post_date > day_ago:
                                    recent_posts.append(post)
                                    # Извлекаем данные поста
                                    post_data = self.extract_post_data(post, channel_name)
                                    
                                    # Скачиваем и конвертируем изображения в base64
                                    if post_data["images"]:
                                        for img_url in post_data["images"]:
                                            base64_img = await self.download_image(session, img_url)
                                            if base64_img:
                                                post_data["images_base64"].append({
                                                    "url": img_url,
                                                    "base64": base64_img
                                                })
                                                logger.info(f"Успешно сконвертировано изображение {img_url} в base64")
                                    
                                    all_posts_data.append(post_data)
                                    logger.info(f"Добавлен пост от {post_date} для канала {channel_name}")
                        
                        post_frequency = len(recent_posts)
                        has_links_ratio = posts_with_links / min(len(posts), posts_count) if posts else 0
                        avg_views = total_views / len(posts) if posts else 0
                        
                        metadata = {
                            "subscribers": subscribers,
                            "post_frequency_per_day": post_frequency,
                            "has_links_ratio": has_links_ratio,
                            "average_views": int(avg_views)
                        }
                        
                        logger.info(f"Успешно получены метаданные для канала {channel_url}: {metadata}")
                        return metadata
                    else:
                        logger.error(f"Ошибка при получении страницы канала {preview_url}: {response.status}")
                        return {
                            "subscribers": 0,
                            "post_frequency_per_day": 0,
                            "has_links_ratio": 0,
                            "average_views": 0
                        }
        except Exception as e:
            logger.error(f"Ошибка при получении метаданных для канала {channel_url}: {e}")
            return {
                "subscribers": 0,
                "post_frequency_per_day": 0,
                "has_links_ratio": 0,
                "average_views": 0
            }

    async def ensure_sources_loaded(self, user_id: int = None) -> bool:
        """Убедиться, что источники загружены. Если нет, попытаться загрузить из файла."""
        # Проверяем, есть ли уже источники
        sources = await self.get_sources_async(user_id)
        if sources:
            print(f"Источники уже загружены для пользователя {user_id}: {len(sources)}")
            return True
            
        print(f"Источники не найдены для пользователя {user_id}, пытаемся загрузить из файла")
        # Проверяем наличие файла с источниками
        if os.path.exists("sources.json"):
            print(f"Найден файл sources.json, загружаем источники для пользователя {user_id}")
            success = await self.load_sources_from_json_async("sources.json", user_id if user_id is not None else 0)
            if success:
                sources = await self.get_sources_async(user_id)
                print(f"Загружено {len(sources)} источников из файла для пользователя {user_id}")
                return True
            else:
                print(f"Не удалось загрузить источники из файла для пользователя {user_id}")
                return False
        else:
            # Создаем файл с базовыми источниками в новом формате
            print(f"Файл sources.json не найден, создаем с базовыми источниками")
            default_sources = [
                "finanz_ru",
                "rbc_economics",
                "profinance_ru",
                "vestifin",
                "banksta",
                "cbrstocks",
                "centralbank_russia",
                "multievan",
                "prostoecon"
            ]
            
            user_id_str = str(user_id if user_id is not None else 0)
            sources_json = {
                "users": {
                    user_id_str: {
                        "sources": []
                    }
                },
                "default_sources": default_sources
            }
            
            with open("sources.json", "w", encoding="utf-8") as f:
                json.dump(sources_json, f, ensure_ascii=False, indent=2)
            
            print(f"Создан файл sources.json с {len(default_sources)} базовыми источниками")
            success = await self.load_sources_from_json_async("sources.json", user_id if user_id is not None else 0)
            if success:
                sources = await self.get_sources_async(user_id)
                print(f"Загружено {len(sources)} источников из файла для пользователя {user_id}")
                return True
            else:
                print(f"Не удалось загрузить источники из файла для пользователя {user_id}")
                return False

    async def get_latest_news_async(self, count: int = 10, hours: int = 24, user_id: int = None) -> List[Dict]:
        """Асинхронное получение последних новостей из всех источников пользователя с указанием количества"""
        try:
            # Получаем все последние новости
            all_news = await self.get_latest_news(hours=hours, user_id=user_id)
            
            # Возвращаем только запрошенное количество новостей
            return all_news[:count] if count > 0 else all_news
        except Exception as e:
            print(f"Ошибка при асинхронном получении последних новостей: {e}")
            return []
            
    async def _scrape_channel(self, session: aiohttp.ClientSession, channel: str, time_cutoff: datetime) -> List[Dict]:
        """Асинхронный скрапинг канала для получения последних новостей"""
        channel_news = []
        url = f"https://t.me/s/{channel}"
        
        print(f"[DEBUG] Начинаю скрапинг канала {channel}, URL: {url}")
        start_time = time.time()
        
        try:
            headers = {
                'User-Agent': random.choice(self.user_agents),
                'Accept': 'text/html,application/xhtml+xml,application/xml',
                'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
            
            print(f"[DEBUG] Отправляю запрос к каналу {channel} с User-Agent: {headers['User-Agent']}")
            request_start_time = time.time()
            
            async with session.get(url, headers=headers, timeout=30) as response:
                request_time = time.time() - request_start_time
                print(f"[DEBUG] Получен ответ от канала {channel}, статус: {response.status}, время запроса: {request_time:.2f} сек.")
                
                if response.status != 200:
                    print(f"[ERROR] Ошибка при запросе канала {channel}: HTTP {response.status}")
                    print(f"[ERROR] Заголовки ответа: {response.headers}")
                    return []
                    
                html_start_time = time.time()
                html = await response.text()
                html_time = time.time() - html_start_time
                print(f"[DEBUG] Получен HTML для канала {channel}, размер: {len(html)} байт, время получения: {html_time:.2f} сек.")
                
                # Проверяем наличие контента
                if len(html) < 100:
                    print(f"[WARNING] Слишком короткий HTML для канала {channel}: {html[:100]}")
                    return []
                
                parse_start_time = time.time()
                soup = BeautifulSoup(html, 'html.parser')
                parse_time = time.time() - parse_start_time
                print(f"[DEBUG] Время парсинга HTML: {parse_time:.2f} сек.")
                
                # Находим все сообщения канала
                posts_search_start = time.time()
                posts = soup.find_all('div', {'class': 'tgme_widget_message'})
                posts_search_time = time.time() - posts_search_start
                print(f"[DEBUG] Найдено {len(posts)} сообщений для канала {channel}, время поиска: {posts_search_time:.2f} сек.")
                
                if not posts:
                    print(f"[WARNING] Не найдены сообщения для канала {channel}")
                    # Проверяем наличие страницы канала вообще
                    channel_info = soup.find('div', {'class': 'tgme_page_additional'})
                    if channel_info:
                        print(f"[INFO] Информация о канале {channel} найдена: {channel_info.text}")
                    else:
                        print(f"[ERROR] Информация о канале {channel} не найдена, возможно неверное имя канала или блокировка доступа")
                        
                    # Сохраняем HTML для отладки
                    debug_path = f"debug_html_{channel}_{int(time.time())}.html"
                    try:
                        with open(debug_path, 'w', encoding='utf-8') as f:
                            f.write(html)
                        print(f"[DEBUG] Сохранен отладочный HTML в файл: {debug_path}")
                    except Exception as e:
                        print(f"[ERROR] Не удалось сохранить отладочный HTML: {e}")
                    return []
                
                # Анализируем найденные посты
                print(f"[DEBUG] Начинаю обработку {len(posts)} постов из канала {channel}")
                for post_index, post in enumerate(posts):
                    try:
                        post_start_time = time.time()
                        print(f"[DEBUG] Обработка поста #{post_index+1}/{len(posts)} из канала {channel}")
                        
                        # Получаем ID поста для отладки
                        post_id = post.get('data-post-id', 'unknown')
                        print(f"[DEBUG] ID поста #{post_index+1}: {post_id}")
                        
                        # Извлекаем данные поста
                        extract_start_time = time.time()
                        post_data = self.extract_post_data(post, channel)
                        extract_time = time.time() - extract_start_time
                        print(f"[DEBUG] Время извлечения данных поста #{post_index+1}: {extract_time:.2f} сек.")
                        
                        # Отладочная информация о полученных данных
                        text_length = len(post_data['text']) if post_data['text'] else 0
                        print(f"[DEBUG] Результаты извлечения для поста #{post_index+1}:")
                        print(f"  - Текст: {text_length} символов")
                        print(f"  - Дата: {post_data['date']}")
                        print(f"  - Просмотры: {post_data['views']}")
                        print(f"  - Ссылки: {len(post_data['links']) if 'links' in post_data else 0}")
                        print(f"  - Изображения: {len(post_data['images']) if 'images' in post_data else 0}")
                        
                        # Проверяем наличие текста
                        if not post_data['text']:
                            print(f"[WARNING] Пост #{post_index+1} не содержит текста, пропускаем")
                            continue
                        
                        # Проверяем дату
                        if not post_data['date']:
                            print(f"[WARNING] Пост #{post_index+1} не содержит даты, пропускаем")
                            continue
                            
                        # Обеспечиваем, что дата поста имеет timezone
                        post_date = post_data['date']
                        if post_date.tzinfo is None:
                            post_date = pytz.UTC.localize(post_date)
                            print(f"[DEBUG] Добавлен часовой пояс UTC к дате поста #{post_index+1}")
                            
                        # Сравниваем даты с учетом часовых поясов
                        time_diff = post_date - time_cutoff
                        hours_diff = time_diff.total_seconds() / 3600
                        print(f"[DEBUG] Пост #{post_index+1} от {post_date}, разница со временем отсечения: {hours_diff:.2f} часов")
                        
                        if post_date < time_cutoff:
                            print(f"[INFO] Пост #{post_index+1} слишком старый (до {time_cutoff}), пропускаем")
                            continue
                            
                        # Добавляем пост в список новостей
                        news_item = {
                            'source': channel,
                            'text': post_data['text'],
                            'date': post_date,
                            'url': post_data['post_url'],
                            'views': post_data['views'],
                            'links': post_data.get('links', []),
                            'images': post_data.get('images', [])
                        }
                        channel_news.append(news_item)
                        print(f"[SUCCESS] Пост #{post_index+1} успешно добавлен в список новостей")
                        
                        post_time = time.time() - post_start_time
                        print(f"[DEBUG] Общее время обработки поста #{post_index+1}: {post_time:.2f} сек.")
                        
                    except Exception as e:
                        error_info = traceback.format_exc()
                        print(f"[ERROR] Ошибка при обработке поста #{post_index+1} из канала {channel}:")
                        print(f"{error_info}")
                        continue
                
                total_time = time.time() - start_time
                print(f"[DEBUG] Обработка канала {channel} завершена, получено {len(channel_news)} новостей, общее время: {total_time:.2f} сек.")
                        
        except aiohttp.ClientError as e:
            print(f"[ERROR] Ошибка клиента при подключении к каналу {channel}: {e}")
            error_details = traceback.format_exc()
            print(f"[ERROR] Детали ошибки: {error_details}")
            
        except asyncio.TimeoutError:
            print(f"[ERROR] Превышено время ожидания при подключении к каналу {channel}")
            
        except Exception as e:
            print(f"[ERROR] Неизвестная ошибка при парсинге канала {channel}: {e}")
            error_details = traceback.format_exc()
            print(f"[ERROR] Детали ошибки: {error_details}")
            
        # Возвращаем результаты
        print(f"[INFO] Канал {channel} вернул {len(channel_news)} новостей")
        return channel_news