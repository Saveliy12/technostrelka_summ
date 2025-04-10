"""
Модуль инициализации для интеграции веб-модуля дайджестов в другие приложения.
Этот модуль предоставляет простой способ добавить функционал дайджестов в существующее Flask приложение.
"""

import os
import json
from flask import Blueprint, jsonify, request, render_template, current_app
import asyncio
from new_generator import DigestStyle, NewsAnalyzer, DigestGenerator
from db_manager import MongoDBManager
from news_aggregator import NewsAggregator

# Предварительное создание Blueprint с указанием всех параметров
digest_bp = Blueprint('digest', __name__, 
                      template_folder='templates',
                      static_folder='static',
                      url_prefix='/digest')

# Определение функций-обработчиков маршрутов - до регистрации в приложении
@digest_bp.route('/')
def index():
    """Главная страница модуля дайджестов"""
    return render_template('digest_index.html')

@digest_bp.route('/api/generate-digest', methods=['POST'])
def generate_digest():
    """API для генерации дайджеста"""
    try:
        data = request.json
        
        # Получаем экземпляр DigestModuleIntegration из текущего приложения
        digest_module = current_app.digest_module
        
        # Получаем параметры из запроса или используем значения по умолчанию
        style_name = data.get('style', 'standard')
        news_count = int(data.get('news_count', digest_module.news_count))
        include_analysis = data.get('include_analysis', digest_module.include_analysis)
        
        # Преобразуем строковое название стиля в DigestStyle
        try:
            style = DigestStyle(style_name)
        except ValueError:
            style = DigestStyle.STANDARD
        
        # Асинхронно запускаем генерацию дайджеста
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        digest_result = loop.run_until_complete(
            digest_module._generate_digest_async(news_count, style, include_analysis)
        )
        loop.close()
        
        return jsonify(digest_result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@digest_bp.route('/api/styles', methods=['GET'])
def get_styles():
    """API для получения списка доступных стилей"""
    # Получаем экземпляр DigestModuleIntegration из текущего приложения
    digest_module = current_app.digest_module
    
    styles = [
        {'id': style.value, 'name': style.name, 'description': digest_module._get_style_description(style)}
        for style in DigestStyle
    ]
    return jsonify(styles)

@digest_bp.route('/api/sources', methods=['GET'])
def get_sources():
    """API для получения списка доступных источников"""
    # Получаем экземпляр DigestModuleIntegration из текущего приложения
    digest_module = current_app.digest_module
    
    sources = digest_module.db_manager.get_all_sources()
    return jsonify(sources)

@digest_bp.route('/api/sources', methods=['POST'])
def add_source():
    """API для добавления нового источника"""
    try:
        # Получаем экземпляр DigestModuleIntegration из текущего приложения
        digest_module = current_app.digest_module
        
        data = request.json
        username = data.get('username')
        name = data.get('name')
        
        if not username:
            return jsonify({'error': 'Имя пользователя не указано'}), 400
        
        # Добавляем источник
        result = digest_module.db_manager.add_source(username, name)
        
        if result:
            # Обновляем кэш источников в агрегаторе
            digest_module.news_aggregator._load_sources_from_db()
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'Источник уже существует или произошла ошибка'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@digest_bp.route('/api/sources/<username>', methods=['DELETE'])
def remove_source(username):
    """API для удаления источника"""
    try:
        # Получаем экземпляр DigestModuleIntegration из текущего приложения
        digest_module = current_app.digest_module
        
        result = digest_module.db_manager.remove_source(username)
        
        if result:
            # Обновляем кэш источников в агрегаторе
            digest_module.news_aggregator._load_sources_from_db()
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'Источник не найден'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

class DigestModuleIntegration:
    """Класс для интеграции модуля дайджестов в существующее Flask приложение"""
    
    def __init__(self, app=None, api_key=None, db_manager=None, news_aggregator=None):
        """
        Инициализация модуля интеграции дайджестов
        
        Args:
            app: Экземпляр Flask приложения
            api_key: API ключ для Mistral AI
            db_manager: Экземпляр MongoDBManager (если None, будет создан новый)
            news_aggregator: Экземпляр NewsAggregator (если None, будет создан новый)
        """
        self.app = app
        self.api_key = api_key or os.getenv('MISTRAL_API_KEYS') or os.getenv('MISTRAL_API_KEY')
        
        # Инициализация компонентов
        try:
            self.news_analyzer = NewsAnalyzer(api_key=self.api_key)
            self.digest_generator = DigestGenerator()
            self.db_manager = db_manager or MongoDBManager()
            self.news_aggregator = news_aggregator or NewsAggregator()
        except Exception as e:
            print(f"Ошибка при инициализации компонентов: {e}")
            raise
        
        # Настройки по умолчанию
        self.current_style = DigestStyle.STANDARD
        self.news_count = int(os.getenv('DEFAULT_NEWS_COUNT', 5))
        self.include_analysis = True
        
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app):
        """
        Инициализация Flask приложения с модулем дайджестов
        
        Args:
            app: Экземпляр Flask приложения
        """
        self.app = app
        
        # Сохранение экземпляра модуля в приложении
        app.digest_module = self
        
        # Регистрация Blueprint
        app.register_blueprint(digest_bp)
    
    def _get_style_description(self, style: DigestStyle) -> str:
        """Получение описания стиля дайджеста"""
        descriptions = {
            DigestStyle.STANDARD: "Стандартный стиль с группировкой по категориям",
            DigestStyle.COMPACT: "Компактный стиль с минимумом текста",
            DigestStyle.MEDIA: "Медиа-ориентированный стиль с акцентом на изображения",
            DigestStyle.CARDS: "Карточный стиль, где каждая новость - отдельная карточка",
            DigestStyle.ANALYTICS: "Аналитический стиль с фокусом на анализ трендов",
            DigestStyle.SOCIAL: "Стиль для социальных сетей с хештегами"
        }
        return descriptions.get(style, "Неизвестный стиль")
    
    async def _generate_digest_async(self, news_count=5, style=DigestStyle.STANDARD, include_analysis=True):
        """Асинхронная генерация дайджеста"""
        try:
            # Получаем новости из агрегатора
            raw_news = await self.news_aggregator.get_latest_news_async(count=news_count)
            
            if not raw_news:
                return {'error': 'Не удалось получить новости'}
            
            # Анализируем новости
            analyzed_news = []
            for news_item in raw_news:
                # Анализируем каждую новость
                analysis = await self._analyze_news_item_async(news_item['text'], news_item['url'])
                if analysis:
                    analyzed_news.append(analysis)
            
            # Если нет проанализированных новостей, возвращаем ошибку
            if not analyzed_news:
                return {'error': 'Не удалось проанализировать новости'}
            
            # Генерируем дайджест
            digest_number = 1  # Номер дайджеста (можно настроить)
            digest_text = self.digest_generator.generate_digest(analyzed_news, digest_number, style)
            
            # Если включен анализ, добавляем его
            overall_analysis = None
            if include_analysis:
                overall_analysis = await self._generate_overall_analysis_async(analyzed_news)
            
            return {
                'digest': digest_text,
                'analysis': overall_analysis,
                'analyzed_news': analyzed_news,
                'style': style.value
            }
        except Exception as e:
            print(f"Ошибка при генерации дайджеста: {e}")
            return {'error': str(e)}
    
    async def _analyze_news_item_async(self, raw_text, url=None):
        """Асинхронный анализ отдельной новости"""
        try:
            # Используем asyncio.to_thread для запуска в отдельном потоке
            loop = asyncio.get_event_loop()
            
            # Анализируем текст новости с использованием Mistral AI в отдельном потоке
            analyzed = await loop.run_in_executor(
                None,  # использовать default executor
                lambda: self.news_analyzer.analyze_news(
                    raw_text=raw_text,
                    style=self.current_style
                )
            )
            
            # Добавляем ссылку на источник, если есть
            if url:
                analyzed['link'] = url
            
            return analyzed
        except Exception as e:
            print(f"Ошибка при анализе новости: {e}")
            return None
    
    async def _generate_overall_analysis_async(self, analyzed_news):
        """Асинхронная генерация общего анализа новостей"""
        try:
            # Запускаем тяжелую операцию в отдельном потоке
            loop = asyncio.get_event_loop()
            
            return await loop.run_in_executor(
                None,  # использовать default executor
                lambda: self.news_analyzer.generate_overall_analysis(
                    analyzed_news, 
                    style=self.current_style
                )
            )
        except Exception as e:
            print(f"Ошибка при генерации общего анализа: {e}")
            return None


# Пример использования при интеграции в существующее Flask приложение:
"""
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
""" 