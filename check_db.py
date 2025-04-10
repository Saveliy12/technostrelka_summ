#!/usr/bin/env python3
import os
import json
from dotenv import load_dotenv
from db_manager import MongoDBManager

def check_db_connection():
    print("Проверка соединения с MongoDB...")
    
    # Загружаем переменные окружения
    load_dotenv()
    
    # Проверяем наличие MONGODB_URI
    mongo_uri = os.environ.get("MONGODB_URI")
    if not mongo_uri:
        print("Ошибка: Не найдена переменная окружения MONGODB_URI")
        return False
    
    print(f"MongoDB URI: {mongo_uri[:10]}... (частично скрыт)")
    
    try:
        # Инициализируем менеджер базы данных
        db_manager = MongoDBManager()
        print("MongoDB успешно инициализирована")
        
        # Проверяем содержимое базы данных
        user_ids = [0, None]  # Проверяем общие источники и None
        
        for user_id in user_ids:
            sources = db_manager.get_source_usernames(user_id)
            print(f"Источники для пользователя {user_id}: {sources}")
            
            if not sources:
                print(f"Нет источников для пользователя {user_id}")
                
                # Смотрим, есть ли файл с источниками и добавляем их если нужно
                if os.path.exists("sources.json"):
                    print("Найден файл sources.json, импортируем источники...")
                    with open("sources.json", "r", encoding="utf-8") as f:
                        channels = json.load(f)
                    
                    added_count = db_manager.import_from_json(channels, user_id if user_id is not None else 0)
                    print(f"Добавлено {added_count} источников из {len(channels)}")
                    
                    # Проверяем, что источники были добавлены
                    sources = db_manager.get_source_usernames(user_id if user_id is not None else 0)
                    print(f"Источники после импорта: {sources}")
                else:
                    print("Файл sources.json не найден, создаем...")
                    # Создаем файл с базовыми источниками
                    default_sources = [
                        {"name": "Банк России", "url": "https://t.me/centralbank_russia"},
                        {"name": "Мультипликатор", "url": "https://t.me/multievan"},
                        {"name": "Простая экономика", "url": "https://t.me/prostoecon"}
                    ]
                    with open("sources.json", "w", encoding="utf-8") as f:
                        json.dump(default_sources, f, ensure_ascii=False, indent=2)
                    
                    print(f"Создан файл sources.json с {len(default_sources)} базовыми источниками")
                    added_count = db_manager.import_from_json(default_sources, user_id if user_id is not None else 0)
                    print(f"Добавлено {added_count} источников")
            else:
                print(f"Найдено {len(sources)} источников для пользователя {user_id}")
        
        print("Проверка соединения с MongoDB завершена успешно")
        return True
    except Exception as e:
        print(f"Ошибка при подключении к MongoDB: {e}")
        return False

if __name__ == "__main__":
    check_db_connection() 