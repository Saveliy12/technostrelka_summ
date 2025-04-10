import json
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
import re
import pytz
from pathlib import Path
import base64
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import os
from mistralai import Mistral
import time


# Загрузка переменных окружения
load_dotenv()

# Создаем директорию для сохранения данных
data_dir = Path("data")
data_dir.mkdir(exist_ok=True)

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

# Инициализация клиента Mistral
api_keys = json.loads(os.getenv('MISTRAL_API_KEYS'))
client = Mistral(api_key=api_keys[0])  # Используем первый ключ
model = "mistral-embed"

def convert_to_preview_url(url):
    """Преобразует обычный URL канала в URL для превью"""
    if '/s/' not in url:
        parts = url.split('/')
        channel_name = parts[-1]
        return f"https://t.me/s/{channel_name}"
    return url

def parse_number(text):
    """Парсит число из текста, обрабатывая суффиксы K и M"""
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

async def download_image(session, url):
    """Скачивает изображение и конвертирует его в base64"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
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

def extract_post_data(post, channel_name):
    """Извлекает данные из поста"""
    # Получаем текст поста
    text_elem = post.find('div', {'class': 'tgme_widget_message_text'})
    text = text_elem.get_text() if text_elem else ""
    
    # Получаем дату
    date_elem = post.find('time')
    date = None
    if date_elem and date_elem.get('datetime'):
        date = date_elem['datetime']
    
    # Получаем просмотры
    views_elem = post.find('span', {'class': 'tgme_widget_message_views'})
    views = parse_number(views_elem.text.strip()) if views_elem else 0
    
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
    
    # Получаем ID поста и ссылку на пост
    post_link = post.find('a', {'class': 'tgme_widget_message_date'})
    post_id = None
    post_url = None
    if post_link and post_link.get('href'):
        post_url = post_link['href']
        post_id = post_url.split('/')[-1]
    
    # Получаем изображения
    images = []
    # Исключаем аватар канала и фото пользователей
    excluded_classes = ['tgme_widget_message_author_photo', 'tgme_widget_message_user_photo']
    

    # Ищем изображения в тегах tgme_widget_message_photo_wrap
    for img_wrap in post.find_all('a', {'class': 'tgme_widget_message_photo_wrap'}):
        # Извлекаем URL изображения из атрибута style
        style = img_wrap.get('style', '')
        if 'background-image:url(' in style:
            # Извлекаем URL из строки background-image:url('...')
            img_url = style.split("background-image:url('")[1].split("')")[0]
            images.append(img_url)
    
    # Также ищем обычные изображения
    for img in post.find_all(['img', 'a']):
        # Проверяем, не находится ли изображение внутри тега i с классом tgme_page_photo_image или tgme_widget_message_user_photo
        parent_i = img.find_parent('i', {'class': ['tgme_page_photo_image', 'tgme_widget_message_user_photo']})
        if parent_i:
            continue
            
        # Проверяем тег img
        if img.name == 'img' and img.get('src'):
            # Исключаем аватар канала и фото пользователей
            if not any(cls in img.get('class', []) for cls in excluded_classes):
                images.append(img['src'])
        # Проверяем ссылки на изображения
        elif img.name == 'a' and img.get('href'):
            href = img['href']
            # Исключаем ссылки на аватар канала и фото пользователей
            if not any(cls in img.get('class', []) for cls in excluded_classes) and any(href.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif']):
                images.append(href)
    
    return {
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

# 2. Функция для парсинга метаданных канала через веб-скрапинг
async def get_channel_metadata_web(channel_url, channel_name, all_posts_data, posts_count=POSTS_TO_ANALYZE, days_to_analyze=2):
    try:
        preview_url = convert_to_preview_url(channel_url)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
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
                            subscribers = parse_number(match.group(1))
                    
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
                            views = parse_number(views_elem.text.strip())
                            total_views += views
                        
                        # Проверяем дату поста
                        date_elem = post.find('time')
                        if date_elem and date_elem.get('datetime'):
                            post_date = datetime.fromisoformat(date_elem['datetime'].replace('Z', '+00:00'))
                            if post_date > day_ago:
                                recent_posts.append(post)
                                # Извлекаем данные поста
                                post_data = extract_post_data(post, channel_name)
                                
                                # Скачиваем и конвертируем изображения в base64
                                if post_data["images"]:
                                    for img_url in post_data["images"]:
                                        base64_img = await download_image(session, img_url)
                                        if base64_img:
                                            post_data["images_base64"].append({
                                                "url": img_url,
                                                "base64": base64_img
                                            })
                                            logger.info(f"Успешно сконвертировано изображение {img_url} в base64")
                                
                                all_posts_data.append(post_data)
                                logger.info(f"Добавлен пост от {post_date} для канала {channel_name}")
                    
                    post_frequency = len(recent_posts)
                    has_links_ratio = posts_with_links / min(len(posts), posts_count) if posts else 0  # Используем переданное количество постов
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

# 3. Функция для загрузки списка каналов из JSON
def load_channels_from_json(json_str):
    try:
        channels = json.loads(json_str)
        return {channel["name"]: channel["url"] for channel in channels}
    except Exception as e:
        logger.error(f"Ошибка при загрузке JSON: {e}")
        return {}

# 4. Функция для обновления метаданных каналов
async def update_channels_metadata(channels_json):
    channels_urls = load_channels_from_json(channels_json)
    channels_meta = {}
    all_posts_data = []  # Список для хранения всех постов
    channels_data = {
        "metadata": {
            "last_update": datetime.now(pytz.UTC).isoformat(),
            "total_channels": len(channels_urls)
        }
    }
    
    for name, url in channels_urls.items():
        logger.info(f"Получение метаданных для канала {name}")
        channel_id = url.split('/')[-1].lower()
        
        # Получаем метаданные и посты за последние 24 часа
        posts_data = []
        metadata = await get_channel_metadata_web(url, name, posts_data)
        channels_meta[name] = metadata
        
        # Создаем запись о канале
        channels_data[name] = {
            "info": {
                "name": name,
                "url": url,
                "subscribers": metadata["subscribers"],
                "post_frequency": metadata["post_frequency_per_day"],
                "average_views": metadata["average_views"]
            },
            "posts": posts_data  # Используем полученные посты
        }
        
        # Добавляем задержку между запросами
        await asyncio.sleep(1)
    
    # Сохраняем данные в файл
    if channels_data:
        channels_file = data_dir / "channels_data.json"
        with open(channels_file, 'w', encoding='utf-8') as f:
            json.dump(channels_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Данные каналов успешно сохранены в файл channels_data.json: "
                   f"{len(channels_urls)} каналов")
    
    return channels_meta

# 5. Оценка источника (автоматически по метаданным)
def estimate_source_weight(channel_info):
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

def calculate_post_relevance(post, channel_weight, max_views):
    """Рассчитывает релевантность поста на основе нескольких факторов"""
    try:
        # Оценка актуальности по времени
        post_date = datetime.fromisoformat(post["date"].replace('Z', '+00:00'))
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

def get_text_embedding(texts, batch_size=5):
    """Получение эмбеддингов через Mistral API с учетом ограничений"""
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
                time.sleep(2)  # Увеличиваем задержку до 1.2 секунд между запросами
        
        return all_embeddings
    except Exception as e:
        logger.error(f"Ошибка при получении эмбеддингов: {e}")
        return None

def find_similar_posts(posts, threshold=SIMILARITY_THRESHOLD, batch_size=5):
    """Находит семантически похожие посты используя Mistral эмбеддинги"""
    # Получаем тексты постов
    texts = [post["text"] for post in posts]
    
    # Получаем эмбеддинги для всех текстов с батчингом
    embeddings = get_text_embedding(texts, batch_size=batch_size)
    if not embeddings:
        return []
    
    # Вычисляем попарную схожесть
    from sklearn.metrics.pairwise import cosine_similarity
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

def select_best_post(group_indices, posts, channel_weights):
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

async def analyze_and_sort_posts(json_file_path):
    """Анализирует, удаляет дубликаты и сортирует посты по достоверности"""
    try:
        # Загружаем данные из JSON
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Собираем все посты и рассчитываем веса каналов
        all_posts = []
        channel_weights = {}
        
        for channel_name, channel_data in data.items():
            if channel_name != "metadata":
                channel_info = channel_data["info"]
                channel_weights[channel_name] = estimate_source_weight({
                    "subscribers": channel_info["subscribers"],
                    "post_frequency_per_day": channel_info["post_frequency"],
                    "has_links_ratio": 0.5,
                    "average_views": channel_info["average_views"]
                })
                
                all_posts.extend(channel_data["posts"])
        
        # Находим группы похожих постов
        similar_groups = find_similar_posts(all_posts)
        
        # Выбираем лучшие посты из каждой группы
        unique_posts = []
        for group in similar_groups:
            best_post = select_best_post(group, all_posts, channel_weights)
            unique_posts.append(best_post)
        
        # Рассчитываем итоговый вес для каждого уникального поста
        posts_with_weight = []
        max_views = max(post["views"] for post in unique_posts) if unique_posts else 1
        
        for post in unique_posts:
            weight = calculate_post_relevance(
                post,
                channel_weights.get(post["channel"], 0),
                max_views
            )
            posts_with_weight.append((post, weight))
        
        # Сортируем посты по весу
        sorted_posts = sorted(posts_with_weight, key=lambda x: x[1], reverse=True)
        
        # Подготавливаем данные для JSON
        sorted_posts_json = {
            "metadata": {
                "last_update": datetime.now(pytz.UTC).isoformat(),
                "total_posts": len(sorted_posts),
                "unique_posts": len(unique_posts)
            },
            "posts": []
        }
        
        # Выводим результаты и сохраняем в JSON
        print("\nУникальные посты, отсортированные по релевантности:")
        print("=" * 80)
        
        for i, (post, weight) in enumerate(sorted_posts, 1):
            post_date = datetime.fromisoformat(post["date"].replace('Z', '+00:00'))
            formatted_date = post_date.strftime("%d.%m.%Y %H:%M")
            
            # Подготавливаем данные поста для JSON
            post_data = {
                "rank": i,
                "channel": post["channel"],
                "date": formatted_date,
                "views": post["views"],
                "weight": round(weight, 3),
                "text": post["text"],
                "links": post["links"],
                "post_url": post.get("post_url", ""),
                "post_id": post.get("post_id", "")
            }
            sorted_posts_json["posts"].append(post_data)
        
        # Сохраняем отсортированные посты в JSON
        sorted_posts_file = data_dir / "sorted_posts.json"
        with open(sorted_posts_file, 'w', encoding='utf-8') as f:
            json.dump(sorted_posts_json, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Отсортированные посты сохранены в файл {sorted_posts_file}")
        
        return sorted_posts
        
    except Exception as e:
        logger.error(f"Ошибка при анализе постов: {e}")
        return []

def calculate_cosine_similarity(text1, text2):
    """Вычисляет косинусное сходство между двумя текстами"""
    vectorizer = TfidfVectorizer()
    try:
        tfidf_matrix = vectorizer.fit_transform([text1, text2])
        return cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])[0][0]
    except:
        return 0.0

def merge_similar_posts(posts, similarity_threshold=MERGE_SIMILARITY_THRESHOLD):
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
                
            similarity = calculate_cosine_similarity(post1["text"], post2["text"])
            if similarity >= similarity_threshold:
                current_group.append(post2)
                used_indices.add(j)
        
        if len(current_group) > 1:
            # Объединяем посты из группы
            merged_post = merge_post_group(current_group)
            merged_posts.append(merged_post)
        else:
            merged_posts.append(post1)
    
    return merged_posts

def is_advertisement(text, links):
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

def is_economics_related(text):
    """Проверяет релевантность поста экономической тематике используя Mistral"""
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
    text_embedding = get_text_embedding([text], batch_size=1)
    if not text_embedding:
        return False, 0, {}
    
    text_embedding = text_embedding[0]
    
    # Получаем эмбеддинги для эталонных текстов по категориям
    category_embeddings = {}
    for category, refs in reference_texts.items():
        # Обрабатываем эталонные тексты небольшими батчами
        ref_embeddings = get_text_embedding(refs, batch_size=2)
        if not ref_embeddings:
            continue
        category_embeddings[category] = ref_embeddings
    
    # Вычисляем схожесть с эталонными текстами
    from sklearn.metrics.pairwise import cosine_similarity
    scores = {}
    
    for category, ref_embeddings in category_embeddings.items():
        similarities = cosine_similarity([text_embedding], ref_embeddings)[0]
        scores[category] = max(similarities)
    
    # Вычисляем итоговый score
    total_score = sum(score * ECONOMICS_WEIGHTS[category] 
                     for category, score in scores.items())
    
    return total_score > ECONOMICS_RELEVANCE_THRESHOLD, total_score, scores

def get_post_type(category_scores):
    """
    Определяет тип поста на основе scores категорий
    """
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

def merge_post_group(posts):
    """Объединяет группу похожих постов в один пост"""
    # Берем пост с наибольшим весом как основной
    main_post = max(posts, key=lambda x: x["weight"])
    
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
    
    # Объединяем изображения
    all_images = set()
    all_images_base64 = []
    for post in posts:
        all_images.update(post.get("images", []))
        all_images_base64.extend(post.get("images_base64", []))
    merged_post["images"] = list(all_images)
    merged_post["images_base64"] = all_images_base64
    
    # Проверяем на рекламу
    is_ad, ad_score = is_advertisement(merged_post["text"], merged_post["links"])
    merged_post["is_advertisement"] = is_ad
    merged_post["ad_score"] = round(ad_score, 3)
    
    # Проверяем на релевантность экономической тематике
    is_econ, econ_score, category_scores = is_economics_related(merged_post["text"])
    merged_post["is_economics_related"] = is_econ
    merged_post["economics_score"] = round(econ_score, 3)
    merged_post["category_scores"] = {k: round(v, 3) for k, v in category_scores.items()}
    merged_post["post_type"] = get_post_type(category_scores)
    
    # Добавляем информацию о слиянии
    merged_post["merged_from"] = len(posts)
    merged_post["original_posts"] = [
        {
            "channel": post["channel"],
            "date": post["date"],
            "views": post["views"],
            "post_url": post.get("post_url", ""),
            "is_advertisement": is_advertisement(post.get("text", ""), post.get("links", []))[0] if post.get("text", "").strip() else False
        } for post in posts
    ]
    
    return merged_post

def get_next_api_key():
    """Получает следующий API ключ из списка"""
    api_keys = json.loads(os.getenv('MISTRAL_API_KEYS'))
    current_key = client.api_key
    current_index = api_keys.index(current_key)
    next_index = (current_index + 1) % len(api_keys)
    return api_keys[next_index]

def handle_api_error(func):
    """Декоратор для обработки ошибок API"""
    def wrapper(*args, **kwargs):
        max_retries = len(json.loads(os.getenv('MISTRAL_API_KEYS')))
        for _ in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Ошибка API: {e}")
                client.api_key = get_next_api_key()
        return None
    return wrapper

async def main(posts_count=POSTS_TO_ANALYZE, days_to_analyze=2):
    """
    Основная функция для сбора и анализа постов из Telegram-каналов
    
    Args:
        posts_count (int): Количество постов, которые должны быть в итоговом JSON
        days_to_analyze (int): Количество дней для анализа постов (по умолчанию 2)
    """
    # JSON с каналами
    channels_json = '''[
                {
                    "name": "Банк России",
                    "url": "https://t.me/centralbank_russia"
                },
                {
                    "name": "Мультипликатор",
                    "url": "https://t.me/multievan"
                },
                {
                    "name": "Ozon Банк",
                    "url": "https://t.me/ozon_bank_official"
                },
                {
                    "name": "Простая экономика",
                    "url": "https://t.me/prostoecon"
                },
                {
                    "name": "Суверенная экономика",
                    "url": "https://t.me/suverenka"
                },
                {
                    "name": "Альфа-Инвестиции",
                    "url": "https://t.me/alfa_investments"
                },
                {
                    "name": "Т-Инвестиции",
                    "url": "https://t.me/tb_invest_official"
                },
                {
                    "name": "СЛЕЗЫ САТОШИ",
                    "url": "https://t.me/slezisatoshi"
                },
                {
                    "name": "Почта Банк",
                    "url": "https://t.me/pochtabank"
                },
                {
                    "name": "Газпромбанк",
                    "url": "https://t.me/gazprombank"
                },
                {
                    "name": "PIFAGOR TRADE",
                    "url": "https://t.me/pifagortrade"
                },
                {
                    "name": "Дерипаска",
                    "url": "https://t.me/olegderipaska"
                },
                {
                    "name": "Trader 80/20",
                    "url": "https://t.me/tradertrend"
                },
                {
                    "name": "На пенсию в 35 лет",
                    "url": "https://t.me/pensiya35"
                },
                {
                    "name": "Coin Post",
                    "url": "https://t.me/coin_post"
                },
                {
                    "name": "Больная экономика",
                    "url": "https://t.me/bolecon"
                },
                {
                    "name": "КриптоБош",
                    "url": "https://t.me/cryptobosh"
                },
                {
                    "name": "bitkogan",
                    "url": "https://t.me/bitkogan"
                },
                {
                    "name": "INSTARDING",
                    "url": "https://t.me/instarding"
                },
                {
                    "name": "Банк РНКБ",
                    "url": "https://t.me/rncb_official"
                },
                {
                    "name": "СберИнвестиции",
                    "url": "https://t.me/sberinvestments"
                },
                {
                    "name": "Пауки в банке",
                    "url": "https://t.me/bankuyte"
                },
                {
                    "name": "ОТП Банк",
                    "url": "https://t.me/otpbanknews"
                },
                {
                    "name": "Профита нет. А если найду?",
                    "url": "https://t.me/profitanet"
                },
                {
                    "name": "Топор. Экономика.",
                    "url": "https://t.me/+OTgn6m2qBDw4ZGNi"
                }
            ]
            '''
    
    # Создаем структуру для хранения всех постов
    all_posts_data = []
    channels_meta = {}
    channels_urls = load_channels_from_json(channels_json)
    
    # Определяем, сколько постов нужно собрать с каждого канала
    # Берем больше постов, чтобы после фильтрации осталось достаточно
    posts_per_channel = max(20, posts_count * 2)  # Автоматически определяем количество постов для сбора
    
    # Получаем данные каналов и посты
    for name, url in channels_urls.items():
        logger.info(f"Получение метаданных для канала {name}")
        posts_data = []
        metadata = await get_channel_metadata_web(url, name, posts_data, posts_per_channel, days_to_analyze)
        channels_meta[name] = metadata
        all_posts_data.extend(posts_data)
        await asyncio.sleep(1)
    
    # Рассчитываем веса каналов
    channel_weights = {
        name: estimate_source_weight(info)
        for name, info in channels_meta.items()
    }
    
    # Находим группы похожих постов с меньшим размером батча
    similar_groups = find_similar_posts(all_posts_data, batch_size=5)
    
    # Выбираем лучшие посты из каждой группы
    unique_posts = []
    for group in similar_groups:
        best_post = select_best_post(group, all_posts_data, channel_weights)
        unique_posts.append(best_post)
    
    # Рассчитываем итоговый вес для каждого уникального поста
    posts_with_weight = []
    max_views = max(post["views"] for post in unique_posts) if unique_posts else 1
    
    for post in unique_posts:
        weight = calculate_post_relevance(
            post,
            channel_weights.get(post["channel"], 0),
            max_views
        )
        posts_with_weight.append((post, weight))
    
    # Сортируем посты по весу
    sorted_posts = sorted(posts_with_weight, key=lambda x: x[1], reverse=True)
    
    # Объединяем похожие посты
    merged_posts = []
    for post in merge_similar_posts([post for post, _ in sorted_posts]):
        if post is not None:  # Проверяем, что пост не был отфильтрован
            merged_posts.append(post)
    
    # Фильтруем рекламные посты с высоким рейтингом
    filtered_posts = []
    for post in merged_posts:
        # Пропускаем посты без текста
        if not post.get("text", "").strip():
            continue
            
        is_ad, ad_score = is_advertisement(post["text"], post.get("links", []))
        post["is_advertisement"] = is_ad
        post["ad_score"] = round(ad_score, 3)
        
        # Пропускаем посты, которые точно реклама
        if is_ad and ad_score > 0.6:
            continue
            
        filtered_posts.append(post)
    
    # Ограничиваем количество постов в итоговом JSON
    if len(filtered_posts) > posts_count:
        filtered_posts = filtered_posts[:posts_count]
    
    # Подготавливаем данные для JSON
    sorted_posts_json = {
        "metadata": {
            "last_update": datetime.now(pytz.UTC).isoformat(),
            "total_posts": len(sorted_posts),
            "unique_posts": len(filtered_posts),
            "ad_posts_filtered": len(merged_posts) - len(filtered_posts),
            "economics_relevance_threshold": ECONOMICS_RELEVANCE_THRESHOLD,
            "days_analyzed": days_to_analyze,
            "posts_count": posts_count,  # Добавляем информацию о запрошенном количестве постов
            "post_types": {
                "экономика": "Посты о макроэкономике, экономическом росте, инфляции и т.д.",
                "финансы": "Посты о финансовой системе, бюджете, налогах и т.д.",
                "банки": "Посты о банковском секторе, кредитах, депозитах и т.д.",
                "инвестиции": "Посты об инвестициях, инвестиционных проектах и т.д.",
                "рынки": "Посты о фондовом, товарном и других рынках",
                "смешанный": "Посты, относящиеся к нескольким категориям",
                "общий": "Посты с недостаточно выраженной тематикой"
            },
            "advertisement": {
                "description": "Поля для определения рекламного контента",
                "is_advertisement": "Флаг, указывающий является ли пост рекламным (true/false)",
                "ad_score": "Оценка вероятности того, что пост является рекламой (0-1)",
                "threshold": "Порог для фильтрации рекламных постов (0.8)"
            }
        },
        "channels": {
            name: {
                "name": name,
                "url": url,
                "weight": channel_weights[name],
                "subscribers": channels_meta[name]["subscribers"],
                "post_frequency": channels_meta[name]["post_frequency_per_day"],
                "average_views": channels_meta[name]["average_views"]
            } for name, url in channels_urls.items()
        },
        "posts": []
    }
    
    # Добавляем отфильтрованные посты в JSON
    for i, post in enumerate(filtered_posts, 1):
        post["rank"] = i
        # Убедимся, что post_type присутствует
        if "post_type" not in post:
            is_econ, _, category_scores = is_economics_related(post["text"])
            post["post_type"] = get_post_type(category_scores)
        sorted_posts_json["posts"].append(post)
    
    # Сохраняем отсортированные посты в JSON
    sorted_posts_file = data_dir / "sorted_posts.json"
    with open(sorted_posts_file, 'w', encoding='utf-8') as f:
        json.dump(sorted_posts_json, f, ensure_ascii=False, indent=2)
    
    logger.info(f"Отсортированные и объединенные посты сохранены в файл {sorted_posts_file}")
    return sorted_posts_json

