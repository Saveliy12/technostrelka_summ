import os
from typing import List, Dict, Optional
from urllib.parse import quote_plus
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
import motor.motor_asyncio
import asyncio
import logging
import datetime


class MongoDBManager:
    """Менеджер для работы с MongoDB"""
    
    def __init__(self, uri=None):
        """Инициализация подключения к MongoDB"""
        try:
            if uri is None:
                # Используем значение из .env или значение по умолчанию
                self.uri = os.getenv('MONGODB_URI', 'mongodb://127.0.0.1:27017/eco_news_bot')
            else:
                self.uri = uri
                
            # Подключаемся к MongoDB
            self.client = MongoClient(self.uri)
            
            # Получаем имя базы данных из URI
            db_name = self.uri.split('/')[-1]
            self.db = self.client[db_name]
            
            # Создаем коллекции, если они не существуют
            if 'sources' not in self.db.list_collection_names():
                self.db.create_collection('sources')
                
            if 'news' not in self.db.list_collection_names():
                self.db.create_collection('news')
                
            if 'users' not in self.db.list_collection_names():
                self.db.create_collection('users')
                
            if 'web_tokens' not in self.db.list_collection_names():
                self.db.create_collection('web_tokens')
                
            # Создаем индексы для быстрого поиска
            self.db.sources.create_index('username', unique=True)
            self.db.news.create_index('url', unique=True)
            self.db.news.create_index('timestamp')
            self.db.users.create_index('user_id', unique=True)
            self.db.web_tokens.create_index('token', unique=True)
            self.db.web_tokens.create_index('user_id')
            self.db.web_tokens.create_index('expiry', expireAfterSeconds=0)  # TTL индекс для автоудаления
            
            logging.info("MongoDB успешно инициализирована")
        except Exception as e:
            logging.error(f"Ошибка при инициализации MongoDB: {e}")
            raise
            
    def _migrate_data_if_needed(self):
        """Миграция данных из старой коллекции в новую, если это необходимо"""
        try:
            # Проверяем, существует ли старая коллекция
            if "sources" in self.db.list_collection_names():
                # Получаем все источники из старой коллекции
                old_sources = list(self.db["sources"].find({}))
                
                if old_sources:
                    print(f"Найдено {len(old_sources)} источников в старой коллекции. Выполняем миграцию...")
                    
                    # Для каждого источника создаем запись для дефолтного пользователя
                    # Используем 0 как ID для общей коллекции источников
                    default_user_id = 0
                    
                    for source in old_sources:
                        username = source.get("username")
                        name = source.get("name", username)
                        
                        # Проверяем, существует ли уже такой источник для дефолтного пользователя
                        existing = self.db.sources.find_one({
                            "user_id": default_user_id,
                            "username": username
                        })
                        
                        if not existing:
                            # Добавляем источник в новую коллекцию
                            self.db.sources.insert_one({
                                "user_id": default_user_id,
                                "username": username,
                                "name": name,
                                "url": f"https://t.me/s/{username}"
                            })
                    
                    print("Миграция данных завершена успешно")
                    
                    # Переименовываем старую коллекцию для резервного копирования
                    self.db.sources.rename("sources_old")
                    print("Старая коллекция переименована в sources_old")
            
        except Exception as e:
            print(f"Ошибка при миграции данных: {e}")
        
    def add_source(self, username: str, user_id: int, name: Optional[str] = None) -> bool:
        """
        Добавление нового источника новостей (синхронный метод)
        
        Args:
            username: Имя пользователя канала
            user_id: ID пользователя Telegram
            name: Отображаемое имя канала (если отличается от username)
            
        Returns:
            True, если источник успешно добавлен, иначе False
        """
        try:
            # Очищаем username от @ в начале и /s/ из URL
            clean_username = username
            if clean_username.startswith('@'):
                clean_username = clean_username[1:]
            elif "t.me/s/" in clean_username:
                clean_username = clean_username.split("t.me/s/")[-1]
            elif "t.me/" in clean_username:
                clean_username = clean_username.split("t.me/")[-1]
                
            # Проверяем, существует ли уже такой источник для данного пользователя
            existing = self.db.sources.find_one({
                "user_id": user_id,
                "username": clean_username
            })
            
            if existing:
                return False
                
            # Добавляем новый источник
            source_data = {
                "user_id": user_id,
                "username": clean_username,
                "name": name or clean_username,
                "url": f"https://t.me/s/{clean_username}"
            }
            
            self.db.sources.insert_one(source_data)
            return True
        except Exception as e:
            print(f"Ошибка при добавлении источника: {e}")
            return False
    
    async def add_source_async(self, username: str, user_id: int, name: Optional[str] = None) -> bool:
        """
        Асинхронное добавление нового источника новостей
        
        Args:
            username: Имя пользователя канала
            user_id: ID пользователя Telegram
            name: Отображаемое имя канала (если отличается от username)
            
        Returns:
            True, если источник успешно добавлен, иначе False
        """
        try:
            # Используем синхронный метод вместо асинхронных операций MongoDB
            loop = asyncio.get_event_loop()
            
            # Запускаем синхронную функцию в отдельном потоке
            result = await loop.run_in_executor(
                None,  # использовать default executor
                lambda: self.add_source(username, user_id, name)
            )
            
            return result
        except Exception as e:
            print(f"Ошибка при асинхронном добавлении источника: {e}")
            return False
            
    def remove_source(self, username: str, user_id: int) -> bool:
        """
        Удаление источника новостей (синхронный метод)
        
        Args:
            username: Имя пользователя канала
            user_id: ID пользователя Telegram
            
        Returns:
            True, если источник успешно удален, иначе False
        """
        try:
            # Очищаем username от @ в начале и /s/ из URL
            clean_username = username
            if clean_username.startswith('@'):
                clean_username = clean_username[1:]
            elif "t.me/s/" in clean_username:
                clean_username = clean_username.split("t.me/s/")[-1]
            elif "t.me/" in clean_username:
                clean_username = clean_username.split("t.me/")[-1]
                
            # Удаляем источник для конкретного пользователя
            result = self.db.sources.delete_one({
                "user_id": user_id,
                "username": clean_username
            })
            
            return result.deleted_count > 0
        except Exception as e:
            print(f"Ошибка при удалении источника: {e}")
            return False
    
    async def remove_source_async(self, username: str, user_id: int) -> bool:
        """
        Асинхронное удаление источника новостей
        
        Args:
            username: Имя пользователя канала
            user_id: ID пользователя Telegram
            
        Returns:
            True, если источник успешно удален, иначе False
        """
        try:
            # Используем синхронный метод в асинхронном контексте
            loop = asyncio.get_event_loop()
            
            # Запускаем синхронную функцию в отдельном потоке
            result = await loop.run_in_executor(
                None,  # использовать default executor
                lambda: self.remove_source(username, user_id)
            )
            
            return result
        except Exception as e:
            print(f"Ошибка при асинхронном удалении источника: {e}")
            return False
            
    def get_all_sources(self, user_id=None) -> List[Dict]:
        """
        Получение списка всех источников для конкретного пользователя (синхронный метод)
        
        Args:
            user_id: ID пользователя Telegram (опционально). Если не указан, возвращаются все источники.
            
        Returns:
            Список словарей с информацией об источниках
        """
        try:
            if user_id is not None:
                return list(self.db.sources.find({"user_id": user_id}, {"_id": 0}))
            else:
                # Если user_id не указан, возвращаем все источники
                return list(self.db.sources.find({}, {"_id": 0}))
        except Exception as e:
            print(f"Ошибка при получении списка источников: {e}")
            return []
    
    async def get_all_sources_async(self, user_id: int) -> List[Dict]:
        """
        Асинхронное получение списка всех источников для конкретного пользователя
        
        Args:
            user_id: ID пользователя Telegram
            
        Returns:
            Список словарей с информацией об источниках
        """
        try:
            # Используем синхронный метод в асинхронном контексте
            loop = asyncio.get_event_loop()
            
            # Запускаем синхронную функцию в отдельном потоке
            result = await loop.run_in_executor(
                None,  # использовать default executor
                lambda: self.get_all_sources(user_id)
            )
            
            return result
        except Exception as e:
            print(f"Ошибка при асинхронном получении списка источников: {e}")
            return []
            
    def get_source_usernames(self, user_id: int) -> List[str]:
        """
        Получение списка имен пользователей всех источников для конкретного пользователя (синхронный метод)
        
        Args:
            user_id: ID пользователя Telegram
            
        Returns:
            Список имен пользователей
        """
        try:
            sources = self.db.sources.find({"user_id": user_id}, {"username": 1, "_id": 0})
            return [source["username"] for source in sources]
        except Exception as e:
            print(f"Ошибка при получении списка имен источников: {e}")
            return []
    
    async def get_source_usernames_async(self, user_id: int) -> List[str]:
        """
        Асинхронное получение списка имен пользователей всех источников для конкретного пользователя
        
        Args:
            user_id: ID пользователя Telegram
            
        Returns:
            Список имен пользователей
        """
        try:
            # Используем синхронный метод в асинхронном контексте через run_in_executor
            loop = asyncio.get_event_loop()
            
            # Запускаем синхронную функцию в отдельном потоке
            result = await loop.run_in_executor(
                None,  # использовать default executor
                lambda: self.get_source_usernames(user_id)
            )
            
            return result
        except Exception as e:
            print(f"Ошибка при асинхронном получении списка имен источников: {e}")
            return []
            
    def import_from_json(self, sources: List[Dict], user_id: int) -> int:
        """
        Импорт источников из списка словарей (из JSON) для конкретного пользователя (синхронный метод)
        
        Args:
            sources: Список словарей с информацией об источниках
            user_id: ID пользователя Telegram
            
        Returns:
            Количество успешно добавленных источников
        """
        added_count = 0
        for source in sources:
            url = source.get("url", "")
            if "t.me/s/" in url:
                username = url.split("t.me/s/")[-1]
            elif "t.me/" in url:
                username = url.split("t.me/")[-1]
            else:
                continue
                
            if self.add_source(username, user_id, source.get("name")):
                added_count += 1
                
        return added_count
    
    async def import_from_json_async(self, sources: List[Dict], user_id: int) -> int:
        """
        Асинхронный импорт источников из списка словарей (из JSON) для конкретного пользователя
        
        Args:
            sources: Список словарей с информацией об источниках
            user_id: ID пользователя Telegram
            
        Returns:
            Количество успешно добавленных источников
        """
        added_count = 0
        tasks = []
        
        for source in sources:
            url = source.get("url", "")
            if "t.me/s/" in url:
                username = url.split("t.me/s/")[-1]
            elif "t.me/" in url:
                username = url.split("t.me/")[-1]
            else:
                continue
                
            tasks.append(self.add_source_async(username, user_id, source.get("name")))
        
        # Выполняем все задачи параллельно
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Подсчитываем успешные добавления
        for result in results:
            if result is True:  # Успешное добавление
                added_count += 1
                
        return added_count
            
    def close(self):
        """Закрытие соединения с базой данных (синхронный метод)"""
        if hasattr(self, "client"):
            self.client.close()
    
    async def close_async(self):
        """Асинхронное закрытие соединения с базой данных"""
        if hasattr(self, "client"):
            self.client.close()
            
    # Методы для работы с веб-токенами
    def save_web_token(self, user_id, token, username=None, expiry=None):
        """
        Сохраняет токен для доступа к веб-интерфейсу
        
        Args:
            user_id: ID пользователя в Telegram
            token: Токен для доступа к веб-интерфейсу
            username: Имя пользователя (опционально)
            expiry: Время истечения токена (опционально)
            
        Returns:
            True если токен успешно сохранен, иначе False
        """
        try:
            # Если время истечения не указано, устанавливаем 24 часа
            if expiry is None:
                expiry = datetime.datetime.now() + datetime.timedelta(hours=24)
                
            # Создаем документ токена
            token_doc = {
                'user_id': user_id,
                'token': token,
                'created_at': datetime.datetime.now(),
                'expiry': expiry
            }
            
            # Если указан username, добавляем его в документ
            if username:
                token_doc['username'] = username
                
            # Удаляем старые токены пользователя
            self.db.web_tokens.delete_many({'user_id': user_id})
            
            # Вставляем новый токен
            self.db.web_tokens.insert_one(token_doc)
            
            logging.info(f"Токен для пользователя {user_id} успешно сохранен")
            return True
        except Exception as e:
            logging.error(f"Ошибка при сохранении веб-токена: {e}")
            return False
    
    def validate_token(self, token):
        """
        Проверяет валидность токена
        
        Args:
            token: Токен для проверки
            
        Returns:
            Документ пользователя если токен валиден, иначе None
        """
        try:
            # Ищем токен в базе
            token_doc = self.db.web_tokens.find_one({
                'token': token,
                'expiry': {'$gt': datetime.datetime.now()}  # Токен не истек
            })
            
            if token_doc:
                # Получаем данные пользователя
                user_id = token_doc['user_id']
                user_doc = self.db.users.find_one({'user_id': user_id})
                
                # Если документ пользователя не найден, создаем минимальный
                if not user_doc:
                    user_doc = {
                        'user_id': user_id,
                        'username': token_doc.get('username'),
                        'sources': self.get_sources(user_id)
                    }
                
                return user_doc
            
            return None
        except Exception as e:
            logging.error(f"Ошибка при проверке веб-токена: {e}")
            return None
    
    def get_user_sources(self, token):
        """
        Получает источники новостей пользователя по токену
        
        Args:
            token: Токен для доступа к веб-интерфейсу
            
        Returns:
            Список источников пользователя или пустой список
        """
        try:
            user_doc = self.validate_token(token)
            
            if user_doc:
                user_id = user_doc['user_id']
                return self.get_sources(user_id)
            
            return []
        except Exception as e:
            logging.error(f"Ошибка при получении источников пользователя по токену: {e}")
            return []
    
    def get_user_preferences(self, token):
        """
        Получает настройки пользователя по токену
        
        Args:
            token: Токен для доступа к веб-интерфейсу
            
        Returns:
            Словарь с настройками пользователя или пустой словарь
        """
        try:
            user_doc = self.validate_token(token)
            
            if user_doc and 'preferences' in user_doc:
                return user_doc['preferences']
            
            # Возвращаем настройки по умолчанию
            return {
                'news_count': 5,
                'style': 'standard',
                'include_analysis': True
            }
        except Exception as e:
            logging.error(f"Ошибка при получении настроек пользователя по токену: {e}")
            return {}

    def get_sources(self, user_id: int) -> List[str]:
        """
        Получение списка имен пользователей всех источников для конкретного пользователя (синхронный метод)
        Алиас для get_source_usernames для обратной совместимости
        
        Args:
            user_id: ID пользователя Telegram
            
        Returns:
            Список имен пользователей
        """
        return self.get_source_usernames(user_id)

    async def validate_token_async(self, token):
        """
        Асинхронная проверка валидности токена
        
        Args:
            token: Токен для проверки
            
        Returns:
            Документ пользователя если токен валиден, иначе None
        """
        try:
            # Используем синхронный метод в асинхронном контексте
            loop = asyncio.get_event_loop()
            
            # Запускаем синхронную функцию в отдельном потоке
            result = await loop.run_in_executor(
                None,  # использовать default executor
                lambda: self.validate_token(token)
            )
            
            return result
        except Exception as e:
            print(f"Ошибка при асинхронной проверке веб-токена: {e}")
            return None 