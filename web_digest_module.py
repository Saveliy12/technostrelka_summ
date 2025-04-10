import os
from flask import Flask, request, jsonify, render_template, session
from dotenv import load_dotenv
import json
import asyncio
from new_generator import DigestStyle, NewsAnalyzer, DigestGenerator
from db_manager import MongoDBManager
from news_aggregator import NewsAggregator
from typing import Dict, Any, Callable, Coroutine, TypeVar
from datetime import datetime

# Тип возвращаемого значения для обобщения
T = TypeVar('T')

# Функция для безопасного запуска асинхронных задач
def run_async_safely(coro: Coroutine) -> T:
    """
    Запускает асинхронную корутину с созданием event loop при необходимости.
    
    Args:
        coro: Асинхронная корутина для выполнения
        
    Returns:
        Результат выполнения корутины
    """
    try:
        # Пытаемся получить существующий event loop
        loop = asyncio.get_event_loop()
        # Проверяем, запущен ли уже event loop
        if loop.is_running():
            # Если event loop уже запущен, используем asyncio.create_task
            # и ждем результата с помощью sync_to_async или другого механизма
            # В данном случае создаем новый event loop в отдельном потоке
            new_loop = asyncio.new_event_loop()
            result = new_loop.run_until_complete(coro)
            new_loop.close()
            return result
        else:
            # Если loop не запущен, используем его
            return loop.run_until_complete(coro)
    except RuntimeError:
        # Если event loop отсутствует в текущем потоке, создаем новый
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

# Загрузка переменных окружения
load_dotenv()

class DigestWebModule:
    """Модуль для создания дайджестов экономических новостей через веб-интерфейс"""
    
    def __init__(self, host='0.0.0.0', port=5000):
        """
        Инициализация веб-модуля для дайджестов
        
        Args:
            host: Хост для веб-сервера
            port: Порт для веб-сервера
        """
        # Инициализация Flask приложения
        self.app = Flask(__name__, 
                         static_folder='static', 
                         template_folder='templates')
        
        # Настройка секретного ключа для сессий
        self.app.secret_key = os.getenv('SECRET_KEY', 'default_secret_key')
        
        self.host = host
        self.port = port
        
        # Получаем API ключ Mistral из переменной окружения
        mistral_api_keys = os.getenv('MISTRAL_API_KEYS')
        if mistral_api_keys:
            try:
                # Пробуем получить первый ключ из массива JSON
                api_keys = json.loads(mistral_api_keys)
                if isinstance(api_keys, list) and len(api_keys) > 0:
                    mistral_api_key = api_keys[0]
                else:
                    mistral_api_key = mistral_api_keys
            except json.JSONDecodeError:
                mistral_api_key = mistral_api_keys
        else:
            mistral_api_key = os.getenv('MISTRAL_API_KEY')
        
        # Инициализируем компоненты для работы с дайджестами
        try:
            self.news_analyzer = NewsAnalyzer(api_key=mistral_api_key)
            self.digest_generator = DigestGenerator()
            self.db_manager = MongoDBManager()
            self.news_aggregator = NewsAggregator()
        except Exception as e:
            print(f"Ошибка при инициализации компонентов: {e}")
            raise
        
        # Настройки по умолчанию
        self.current_style = DigestStyle.STANDARD
        self.news_count = int(os.getenv('DEFAULT_NEWS_COUNT', 5))
        self.include_analysis = True
        
        # Регистрация маршрутов
        self._register_routes()
    
    def _register_routes(self):
        """Регистрация маршрутов Flask"""
        
        @self.app.route('/')
        def index():
            """Главная страница"""
            # Получаем токен и данные пользователя из параметров URL
            token = request.args.get('token')
            username = request.args.get('username')
            
            # Сохраняем данные пользователя в сессии для использования в других маршрутах
            if token and username:
                session['token'] = token
                session['username'] = username
                
            # Передаем данные пользователя в шаблон
            return render_template('digest_index.html', username=username)
        
        @self.app.route('/api/generate-digest', methods=['POST'])
        def generate_digest():
            """API для генерации дайджеста"""
            try:
                data = request.json
                token = session.get('token') 
                
                # Получаем параметры из запроса или используем значения по умолчанию
                style_name = data.get('style', 'standard')
                news_count = int(data.get('news_count', self.news_count))
                include_analysis = data.get('include_analysis', self.include_analysis)
                
                # Получаем информацию о пользователе для персонализации
                username = session.get('username')
                
                # Если есть токен, получаем персонализированные источники пользователя
                user_id = None
                user_sources = []
                
                # Проверяем токен, если он есть
                if token:
                    try:
                        # Асинхронно проверяем токен
                        user_doc = run_async_safely(
                            self.db_manager.validate_token_async(token)
                        )
                        
                        if user_doc:
                            user_id = user_doc.get('user_id')
                            # Асинхронно получаем имена источников
                            user_sources = run_async_safely(
                                self.db_manager.get_source_usernames_async(user_id)
                            )
                    except Exception as e:
                        print(f"Ошибка при получении источников пользователя: {e}")
                
                # Преобразуем строковое название стиля в DigestStyle
                try:
                    style = DigestStyle(style_name)
                except ValueError:
                    style = DigestStyle.STANDARD
                
                # Передаем источники пользователя, если они есть
                digest_kwargs = {
                    'news_count': news_count, 
                    'style': style, 
                    'include_analysis': include_analysis
                }
                
                if user_id is not None:
                    digest_kwargs['user_id'] = user_id
                
                digest_result = run_async_safely(
                    self._generate_digest_async(**digest_kwargs)
                )
                
                # Добавляем персонализированное приветствие если есть username
                personalized = False
                if username:
                    personalized = True
                    
                return jsonify({
                    'digest': digest_result['digest'],
                    'analysis': digest_result['analysis'],
                    'analyzed_news': digest_result['analyzed_news'],
                    'style': digest_result['style'],
                    'personalized': personalized,
                    'username': username,
                    'user_id': user_id
                })
            except Exception as e:
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/styles', methods=['GET'])
        def get_styles():
            """API для получения списка доступных стилей"""
            styles = [
                {'id': style.value, 'name': style.name, 'description': self._get_style_description(style)}
                for style in DigestStyle
            ]
            return jsonify(styles)
        
        @self.app.route('/api/sources', methods=['GET'])
        def get_sources():
            """API для получения списка доступных источников"""
            try:
                # Получаем токен и id пользователя из сессии
                token = session.get('token')
                
                # Если есть токен, получаем источники конкретного пользователя
                if token:
                    try:
                        # Асинхронно проверяем токен
                        user_doc = run_async_safely(
                            self.db_manager.validate_token_async(token)
                        )
                        
                        if user_doc:
                            user_id = user_doc.get('user_id')
                            
                            # Получаем детали источников асинхронно
                            sources = run_async_safely(
                                self.db_manager.get_all_sources_async(user_id)
                            )
                            
                            # Проверяем формат - если получили просто список имен, преобразуем в объекты
                            if sources and isinstance(sources[0], str):
                                sources = [{'username': username, 'name': username} for username in sources]
                            
                            return jsonify(sources)
                    except Exception as e:
                        print(f"Ошибка при получении источников пользователя: {e}")
                
                # Если нет токена или произошла ошибка, возвращаем все источники
                sources = self.db_manager.get_all_sources()
                
                # Проверяем формат - если получили просто список имен, преобразуем в объекты
                if sources and isinstance(sources[0], str):
                    sources = [{'username': username, 'name': username} for username in sources]
                    
                return jsonify(sources if sources else [])
            except Exception as e:
                print(f"Ошибка при получении источников: {e}")
                # Возвращаем пустой массив вместо ошибки, чтобы клиентский код мог продолжить работу
                return jsonify([])
        
        @self.app.route('/api/sources', methods=['POST'])
        def add_source():
            """API для добавления нового источника"""
            try:
                data = request.json
                username = data.get('username')
                name = data.get('name')
                
                if not username:
                    return jsonify({'error': 'Имя пользователя не указано'}), 400
                
                # Получаем user_id из токена или используем дефолтный
                user_id = None
                token = session.get('token')
                
                if token:
                    try:
                        # Асинхронно проверяем токен
                        user_doc = run_async_safely(
                            self.db_manager.validate_token_async(token)
                        )
                        
                        if user_doc:
                            user_id = user_doc.get('user_id')
                    except Exception as e:
                        print(f"Ошибка при проверке токена: {e}")
                
                # Если не получили user_id, используем дефолтный
                if user_id is None:
                    user_id = 0  # Дефолтный пользователь
                
                # Добавляем источник асинхронно
                result = run_async_safely(
                    self.db_manager.add_source_async(username, user_id, name)
                )
                
                if result:
                    # Загружаем источники для пользователя в агрегатор асинхронно
                    try:
                        # Асинхронно загружаем источники
                        run_async_safely(
                            self.news_aggregator._load_sources_for_user_async(user_id)
                        )
                    except Exception as e:
                        print(f"Предупреждение: Не удалось обновить кэш источников: {e}")
                
                if result:
                    return jsonify({'success': True})
                else:
                    return jsonify({'error': 'Источник уже существует или произошла ошибка'}), 400
            except Exception as e:
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/sources/<username>', methods=['DELETE'])
        def remove_source(username):
            """API для удаления источника"""
            try:
                # Получаем user_id из токена или используем дефолтный
                user_id = None
                token = session.get('token')
                
                if token:
                    try:
                        # Асинхронно проверяем токен
                        user_doc = run_async_safely(
                            self.db_manager.validate_token_async(token)
                        )
                        
                        if user_doc:
                            user_id = user_doc.get('user_id')
                    except Exception as e:
                        print(f"Ошибка при проверке токена: {e}")
                
                # Если не получили user_id, используем дефолтный
                if user_id is None:
                    user_id = 0  # Дефолтный пользователь
                
                # Удаляем источник асинхронно
                result = run_async_safely(
                    self.db_manager.remove_source_async(username, user_id)
                )
                
                if result:
                    # Загружаем источники для пользователя в агрегатор асинхронно
                    try:
                        # Асинхронно загружаем источники
                        run_async_safely(
                            self.news_aggregator._load_sources_for_user_async(user_id)
                        )
                    except Exception as e:
                        print(f"Предупреждение: Не удалось обновить кэш источников: {e}")
                
                if result:
                    return jsonify({'success': True})
                else:
                    return jsonify({'error': 'Источник не найден'}), 404
            except Exception as e:
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/sources/<username>', methods=['GET'])
        def get_source_by_username(username):
            """API для получения информации об источнике по username"""
            try:
                # Получаем user_id из токена или используем дефолтный
                user_id = None
                token = session.get('token')
                
                if token:
                    try:
                        # Асинхронно проверяем токен
                        user_doc = run_async_safely(
                            self.db_manager.validate_token_async(token)
                        )
                        
                        if user_doc:
                            user_id = user_doc.get('user_id')
                    except Exception as e:
                        print(f"Ошибка при проверке токена: {e}")
                
                # Если не получили user_id, используем дефолтный
                if user_id is None:
                    user_id = 0  # Дефолтный пользователь
                
                # Получаем все источники пользователя асинхронно
                sources = run_async_safely(
                    self.db_manager.get_all_sources_async(user_id)
                )
                
                # Ищем источник по username
                source = next((s for s in sources if s.get('username') == username), None)
                
                if source:
                    return jsonify(source)
                else:
                    return jsonify({'error': 'Источник не найден'}), 404
            except Exception as e:
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/user-info', methods=['GET'])
        def get_user_info():
            """API для получения информации о пользователе"""
            username = session.get('username')
            token = session.get('token')
            
            # Если нет токена или username, возвращаем ошибку авторизации
            if not token or not username:
                return jsonify({'error': 'Не авторизован'}), 401
                
            # Проверяем токен в БД и получаем данные пользователя
            try:
                # Асинхронно проверяем токен
                user_doc = run_async_safely(
                    self.db_manager.validate_token_async(token)
                )
                
                if not user_doc:
                    return jsonify({'error': 'Недействительный токен'}), 401
                    
                user_id = user_doc.get('user_id')
                
                # Получаем источники новостей пользователя асинхронно
                sources = run_async_safely(
                    self.db_manager.get_all_sources_async(user_id)
                )
                
                # Получаем настройки пользователя (синхронно, т.к. нет асинхронной версии)
                preferences = self.db_manager.get_user_preferences(token) or {
                    'news_count': self.news_count,
                    'style': self.current_style.value,
                    'include_analysis': self.include_analysis
                }
                
                return jsonify({
                    'username': username,
                    'user_id': user_id,
                    'sources': sources,
                    'preferences': preferences
                })
            except Exception as e:
                print(f"Ошибка при получении информации о пользователе: {e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/digest', methods=['GET'])
        def digest_page():
            """Страница дайджеста с персонализацией"""
            # Получаем токен и данные пользователя из параметров URL
            token = request.args.get('token')
            username = request.args.get('username')
            
            # Сохраняем данные пользователя в сессии для использования в других маршрутах
            if token and username:
                session['token'] = token
                session['username'] = username
            
            # Получаем данные из сессии, если они не пришли в URL
            username = username or session.get('username')
            
            # Передаем данные пользователя в шаблон
            return render_template('digest_page.html', 
                                  username=username, 
                                  welcome_message=f"Добро пожаловать, {username}!" if username else "Добро пожаловать!")
    
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
    
    async def _generate_digest_async(self, news_count=5, style=DigestStyle.STANDARD, include_analysis=True, user_id=None):
        """
        Асинхронная генерация дайджеста
        
        Args:
            news_count: Количество новостей в дайджесте
            style: Стиль дайджеста
            include_analysis: Включать ли анализ трендов
            user_id: ID пользователя для получения персонализированных источников
        """
        try:
            # Получаем новости из агрегатора
            if user_id is not None:
                # Получаем новости из источников конкретного пользователя
                raw_news = await self.news_aggregator.get_latest_news_async(count=news_count, user_id=user_id)
            else:
                # Получаем новости из всех источников
                raw_news = await self.news_aggregator.get_latest_news_async(count=news_count)
            
            if not raw_news:
                return {'error': 'Не удалось получить новости'}
            
            # Анализируем новости
            analyzed_news = []
            for news_item in raw_news:
                # Анализируем каждую новость
                try:
                    analysis = await self._analyze_news_item_async(user_id=user_id, news_item=news_item)
                    if analysis:
                        analyzed_news.append(analysis)
                except Exception as e:
                    print(f"Ошибка при анализе новости: {e}")
                    # Продолжаем с другими новостями
                    continue
            
            # Если нет проанализированных новостей, возвращаем ошибку
            if not analyzed_news:
                return {'error': 'Не удалось проанализировать новости'}
            
            # Генерируем дайджест, передавая null в параметр overall_analysis если include_analysis=False
            digest_number = 1  # Номер дайджеста (можно настроить)
            
            # Генерация дайджеста с или без анализа в зависимости от параметра include_analysis
            if include_analysis:
                # Анализ уже будет добавлен в дайджест самим DigestGenerator,
                # поэтому нам не нужно генерировать его отдельно здесь
                digest_text = self.digest_generator.generate_digest(analyzed_news, digest_number, style)
                # Генерируем анализ только для отдельного возврата, если требуется
                overall_analysis = await self._generate_overall_analysis_async(analyzed_news)
            else:
                # Создаем временный генератор без анализа
                # Для этого используем подкласс DigestGenerator, который не генерирует анализ
                class NoAnalysisDigestGenerator(DigestGenerator):
                    def generate_digest(self, analyzed_news, digest_number, style=None):
                        # Используем родительский метод, но передаем пустой текст анализа
                        current_style = style or self.style
                        news_by_category = self._group_news_by_category(analyzed_news)
                        return self.template.render(
                            news_by_category=news_by_category,
                            digest_number=digest_number,
                            overall_analysis=""  # Пустой анализ
                        )
                
                # Используем генератор без анализа
                temp_generator = NoAnalysisDigestGenerator(style=style)
                digest_text = temp_generator.generate_digest(analyzed_news, digest_number)
                overall_analysis = None
            
            return {
                'digest': digest_text,
                'analysis': overall_analysis,
                'analyzed_news': analyzed_news,
                'style': style.value
            }
        except Exception as e:
            print(f"Ошибка при генерации дайджеста: {e}")
            return {'error': str(e)}
    
    async def _analyze_news_item_async(self, user_id: str, news_item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Асинхронный анализ отдельной новости.
        """
        try:
            analyzed = await self.news_analyzer.analyze_news_async(
                news_item["text"], 
                news_item.get("image_path"), 
                self.current_style,
                news_item.get("video_link")
            )
            
            # Добавляем ссылку на источник, если есть
            # Проверяем разные возможные имена полей для ссылки
            url = news_item.get("url") or news_item.get("link") or news_item.get("source_url")
            if url:
                analyzed['link'] = url
            
            return analyzed
        except Exception as e:
            print(f"Ошибка при анализе новости: {e}")
            return {
                "category": "Экономика",
                "title": "Ошибка анализа",
                "description": news_item["text"][:100] + ("..." if len(news_item["text"]) > 100 else "")
            }
    
    async def _generate_overall_analysis_async(self, analyzed_news):
        """Асинхронная генерация общего анализа новостей"""
        try:
            # Генерируем общий анализ с использованием Mistral AI
            return await self.news_analyzer.generate_overall_analysis_async(
                news_items=analyzed_news,
                style=self.current_style
            )
        except Exception as e:
            print(f"Ошибка при создании общего анализа: {e}")
            # Возвращаем резервное сообщение в случае ошибки
            return "Анализ текущих новостей показывает смешанную экономическую картину. Следите за дальнейшим развитием событий для принятия взвешенных финансовых решений."
    
    def run(self, debug=False):
        """Запуск Flask-приложения"""
        self.app.run(host=self.host, port=self.port, debug=debug)


if __name__ == "__main__":
    # Запуск веб-модуля при выполнении файла напрямую
    web_module = DigestWebModule()
    web_module.run(debug=True) 