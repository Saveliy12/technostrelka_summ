import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.filters import Command, CommandObject
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
import asyncio
import aiohttp
import re
import json
from news_aggregator import NewsAggregator
from new_generator import DigestStyle, NewsAnalyzer, DigestGenerator
import random
from web_digest_module import DigestWebModule

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Определение состояний пользователя
class UserStates(StatesGroup):
    waiting_for_count = State()          # Ожидание ввода количества новостей
    waiting_for_frequency = State()      # Ожидание ввода частоты обновления
    waiting_for_source = State()         # Ожидание ввода источника для добавления
    waiting_for_username = State()       # Ожидание ввода имени пользователя для веб-интерфейса

class NewsBot:
    def __init__(self):
        self.token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.news_aggregator = NewsAggregator()
        
        # Инициализация веб-модуля
        try:
            flask_host = os.getenv('FLASK_HOST', '0.0.0.0')
            flask_port = int(os.getenv('FLASK_PORT', '5000'))
            self.web_module = DigestWebModule(host=flask_host, port=flask_port)
            self.web_interface_url = os.getenv('WEB_INTERFACE_URL', 'http://localhost:5000/digest')
        except Exception as e:
            logger.error(f"Ошибка при инициализации веб-модуля: {e}")
            self.web_module = None
            self.web_interface_url = None
        
        # Получаем API ключ Mistral из переменной окружения
        mistral_api_keys = os.getenv('MISTRAL_API_KEYS')
        if mistral_api_keys:
            try:
                # Пробуем получить первый ключ из массива JSON
                api_keys = json.loads(mistral_api_keys)
                if isinstance(api_keys, list) and len(api_keys) > 0:
                    mistral_api_key = api_keys[2]
                else:
                    mistral_api_key = mistral_api_keys  # Используем как обычную строку
            except json.JSONDecodeError:
                # Если не JSON, используем как обычную строку
                mistral_api_key = mistral_api_keys
        else:
            mistral_api_key = os.getenv('MISTRAL_API_KEY')
            
        # Инициализируем новые классы с API ключом
        try:
            self.news_analyzer = NewsAnalyzer(api_key=mistral_api_key)
            
            # Создаем отдельный экземпляр NewsAnalyzer для DigestGenerator
            analyzer_for_generator = NewsAnalyzer(api_key=mistral_api_key)
            
            # Инициализируем DigestGenerator без создания нового NewsAnalyzer
            self.digest_generator = DigestGenerator()
            # Заменяем встроенный экземпляр NewsAnalyzer на наш с ключом
            self.digest_generator.analyzer = analyzer_for_generator
        except Exception as e:
            logger.error(f"Ошибка при инициализации Mistral AI: {e}")
            # Создаем заглушки для объектов
            self.news_analyzer = None
            self.digest_generator = None
        
        self.scheduled_jobs = {}
        self.news_count = int(os.getenv('DEFAULT_NEWS_COUNT', 5))
        self.update_frequency = int(os.getenv('DEFAULT_UPDATE_FREQUENCY', 24))
        self.include_analysis = True  # Флаг для включения/отключения анализа
        self.current_style = DigestStyle.STANDARD
        self.last_messages = []  # Хранилище последних сообщений
        
        # Клавиатура с основными командами
        self.main_keyboard = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text='📰 Создать дайджест'), KeyboardButton(text='📋 Мои источники')],
            [KeyboardButton(text='➕ Добавить источник'), KeyboardButton(text='➖ Удалить источник')],
            [KeyboardButton(text='⚙️ Настройки'), KeyboardButton(text='🌐 Веб-интерфейс'), KeyboardButton(text='❓ Помощь')]
        ], resize_keyboard=True)
        
        # Инициализация бота и диспетчера
        self.bot = Bot(token=self.token, parse_mode=ParseMode.HTML)
        # Используем больше воркеров для параллельной обработки запросов
        self.dp = Dispatcher(storage=MemoryStorage())
        # Создаем отдельный класс для обработки тяжелых задач
        self._background_tasks = set()
        
        # Создаем роутер для команд
        self.router = Router()
        self.dp.include_router(self.router)
        
        # Регистрация обработчиков
        self._register_handlers()
        
    def _register_handlers(self):
        # Команды
        self.router.message.register(self.start, Command("start"))
        self.router.message.register(self.help, Command("help"))
        self.router.message.register(self.add_source, Command("add_source"))
        self.router.message.register(self.remove_source, Command("remove_source"))
        self.router.message.register(self.list_sources, Command("list_sources"))
        self.router.message.register(self.search_source, Command("search_source"))
        self.router.message.register(self.import_sources, Command("import_sources"))
        self.router.message.register(self.set_count, Command("set_count"))
        self.router.message.register(self.set_frequency, Command("set_frequency"))
        self.router.message.register(self.generate_digest, Command("generate"))
        self.router.message.register(self.set_style, Command("set_style"))
        self.router.message.register(self.styles, Command("styles"))
        self.router.message.register(self.toggle_analysis, Command("toggle_analysis"))
        self.router.message.register(self.publish_to_channel, Command("publish_to_channel"))
        self.router.message.register(self.settings, Command("settings"))
        self.router.message.register(self.generate_from_source, Command("from_source"))
        self.router.message.register(self.web_interface, Command("web"))
        
        # Обработка текстовых команд с кнопок (более высокий приоритет)
        self.router.message.register(self.generate_digest, F.text == '📰 Создать дайджест')
        self.router.message.register(self.list_sources, F.text == '📋 Мои источники')
        self.router.message.register(self.add_source_menu, F.text == '➕ Добавить источник')
        self.router.message.register(self.remove_source, F.text == '➖ Удалить источник')
        self.router.message.register(self.settings, F.text == '⚙️ Настройки')
        self.router.message.register(self.web_interface, F.text == '🌐 Веб-интерфейс')
        self.router.message.register(self.help, F.text == '❓ Помощь')
        
        # Обработчик данных от веб-приложения
        self.router.message.register(self.process_webapp_data, F.web_app_data)
        
        # Обработчик callback-запросов
        self.router.callback_query.register(self.button_callback)
        
        # Обработчик текстовых сообщений (наименьший приоритет)
        # Должен быть зарегистрирован последним, чтобы не перехватывать команды
        self.router.message.register(self.process_text_input, F.text)

    async def start(self, message: Message):
        """Приветственное сообщение при запуске бота"""
        user_first_name = message.from_user.first_name if message.from_user else "пользователь"
        
        welcome_message = (
            f"👋 Здравствуйте, {user_first_name}!\n\n"
            f"Я бот для агрегации и суммаризации экономических новостей. "
            f"Я могу собирать новости из различных Telegram-каналов, удалять дубликаты, "
            f"ранжировать их по важности и создавать краткие дайджесты.\n\n"
            f"🔍 <b>Основные функции</b>:\n"
            f"• Автоматический сбор новостей из указанных каналов\n"
            f"• Удаление дубликатов и похожих новостей\n"
            f"• Ранжирование новостей по важности\n"
            f"• Создание дайджестов с различными стилями\n"
            f"• Аналитика экономических тенденций\n\n"
            f"Для начала работы добавьте источники новостей с помощью команды /add_source и настройте бота по своему вкусу.\n"
            f"Используйте /help для просмотра полного списка команд."
        )
        
        # Создаем основную клавиатуру
        await message.answer(welcome_message, parse_mode='HTML', reply_markup=self.main_keyboard)
        
        # Если источников нет, предлагаем добавить
        if not self.news_aggregator.get_sources(message.from_user.id):
            await message.answer(
                "🔔 У вас пока нет добавленных источников новостей. "
                "Чтобы начать использовать бота, добавьте хотя бы один источник "
                "с помощью команды /add_source."
            )
            
        # Примеры команд
        examples = (
            "Примеры команд:\n"
            "/add_source @centralbank_russia - добавить канал Банка России\n"
            "/generate - создать дайджест новостей\n"
            "/set_count 5 - установить количество новостей в дайджесте\n"
            "/set_style standard - установить стиль дайджеста"
        )
        
        await message.answer(examples)

    async def help(self, message: Message):
        """Помощь по командам бота"""
        help_text = (
            "🤖 <b>Список доступных команд</b>:\n\n"
            "<b>Настройка источников</b>:\n"
            "/add_source [@username] - добавить канал в источники\n"
            "/remove_source [@username] - удалить канал из источников\n"
            "/list_sources - показать список источников\n"
            "/import_sources - импортировать источники из файла\n\n"
            
            "<b>Настройка дайджеста</b>:\n"
            "/set_count [число] - установить количество новостей (по умолчанию: 5)\n"
            "/set_frequency [часы] - установить частоту обновления (по умолчанию: 24 часа)\n"
            "/set_style [стиль] - установить стиль дайджеста\n"
            "/styles - показать доступные стили дайджеста\n"
            "/toggle_analysis - включить/выключить анализ трендов\n"
            "/settings - открыть меню настроек с кнопками\n\n"
            
            "<b>Генерация и публикация</b>:\n"
            "/generate - создать дайджест прямо сейчас\n"
            "/publish_to_channel [@channel] - опубликовать дайджест в указанный канал\n"
            "/from_source [имя_канала] - создать дайджест из конкретного источника\n\n"
            
            "<b>Справка</b>:\n"
            "/start - перезапустить бота\n"
            "/help - показать это сообщение"
        )
        
        await message.answer(help_text, parse_mode='HTML')
        
        tips = (
            "💡 <b>Полезные советы</b>:\n\n"
            "• Для оптимальной работы бота добавьте 5-10 качественных источников\n"
            "• Экспериментируйте с разными стилями дайджеста для выбора наиболее подходящего\n"
            "• Для регулярного получения дайджестов, добавьте бота в группу или канал и установите частоту обновления\n"
            "• Используйте команду /settings для быстрого доступа к основным настройкам"
        )
        
        await message.answer(tips, parse_mode='HTML')

    async def add_source(self, message: Message, command: CommandObject):
        """Добавление источника новостей"""
        if not command.args:
            await message.answer(
                "❌ Необходимо указать имя канала для добавления.\n\n"
                "Пример: /add_source @channel_name\n"
                "Или: /add_source https://t.me/channel_name\n\n"
                "Канал должен быть публичным."
            )
            return
            
        channel = command.args
        
        # Очищаем имя канала от URL и @
        clean_channel = channel
        if 't.me/' in channel:
            clean_channel = channel.split('t.me/')[-1].split('/')[0]
        elif channel.startswith('@'):
            clean_channel = channel[1:]
            
        # Валидация формата имени канала
        if not re.match(r'^[a-zA-Z0-9_]+$', clean_channel):
            await message.answer(
                f"❌ Неверный формат имени канала: {channel}\n"
                f"Имя канала должно содержать только латинские буквы, цифры и символ подчеркивания."
            )
            return
            
        status_msg = await message.answer(f"⏳ Добавляю канал @{clean_channel}...")
        
        # Получаем ID пользователя
        user_id = message.from_user.id
        
        # Проверяем, можем ли мы получить доступ к каналу
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://t.me/s/{clean_channel}"
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
                
                async with session.get(url, headers=headers, timeout=10) as response:
                    if response.status != 200:
                        await status_msg.edit_text(
                            f"❌ Не удалось получить доступ к каналу @{clean_channel}.\n"
                            f"Убедитесь, что канал существует и является публичным."
                        )
                        return
                    
                    # Проверяем, что страница содержит сообщения канала
                    html = await response.text()
                    if not html or "tgme_widget_message" not in html:
                        await status_msg.edit_text(
                            f"❌ Канал @{clean_channel} не содержит сообщений или является приватным."
                        )
                        return
                    
                    # Канал доступен, добавляем его
                    result = await self.news_aggregator.add_source_async(clean_channel, user_id)
                    
                    if result:
                        await status_msg.edit_text(
                            f"✅ Канал @{clean_channel} успешно добавлен в ваши источники!"
                        )
                    else:
                        await status_msg.edit_text(
                            f"⚠️ Канал @{clean_channel} уже есть в ваших источниках."
                        )
        except Exception as e:
            logger.error(f"Ошибка при добавлении источника: {e}")
            await status_msg.edit_text(
                f"❌ Произошла ошибка при добавлении канала @{clean_channel}.\n"
                f"Пожалуйста, попробуйте позже."
            )

    async def remove_source(self, message: Message, command: CommandObject = None):
        """Удаление источника из списка источников"""
        user_id = message.from_user.id
        
        # Проверяем, есть ли у пользователя источники
        sources = await self.news_aggregator.get_sources_async(user_id)
        if not sources:
            await message.answer("❌ У вас нет добавленных источников.")
            return
            
        # Если команда вызвана без аргументов, показываем список источников для выбора
        if not command or not command.args:
            sources_list = await self.news_aggregator.get_source_details_async(user_id)
            
            # Создаем инлайн-кнопки для каждого источника
            buttons = []
            for source in sources_list:
                button_text = f"@{source['username']}"
                if source.get('name') and source['name'] != source['username']:
                    button_text += f" ({source['name']})"
                    
                buttons.append([InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"remove_source:{source['username']}"
                )])
                
            markup = InlineKeyboardMarkup(inline_keyboard=buttons)
                
            await message.answer(
                "🗑 Выберите источник для удаления:",
                reply_markup=markup
            )
            return
            
        # Если указан источник, удаляем его
        channel = command.args
        
        # Очищаем имя канала от URL и @
        clean_channel = channel
        if 't.me/' in channel:
            clean_channel = channel.split('t.me/')[-1].split('/')[0]
        elif channel.startswith('@'):
            clean_channel = channel[1:]
            
        # Проверяем, есть ли такой канал в списке источников
        if clean_channel not in sources:
            await message.answer(
                f"❌ Канал @{clean_channel} не найден в ваших источниках."
            )
            return
            
        # Удаляем источник
        result = await self.news_aggregator.remove_source_async(clean_channel, user_id)
        
        if result:
            await message.answer(
                f"✅ Канал @{clean_channel} успешно удален из ваших источников!"
            )
        else:
            await message.answer(
                f"❌ Произошла ошибка при удалении канала @{clean_channel}.\n"
                f"Пожалуйста, попробуйте позже."
            )

    async def list_sources(self, message: Message):
        """Отображение списка источников"""
        user_id = message.from_user.id
        
        # Получаем список источников
        sources_list = await self.news_aggregator.get_source_details_async(user_id)
        
        if not sources_list:
            await message.answer(
                "📋 У вас нет добавленных источников новостей.\n\n"
                "Используйте команду /add_source, чтобы добавить новый источник."
            )
            return
            
        # Создаем кнопки для источников
        buttons = []
        
        # Кнопки для действий со всеми источниками
        buttons.append([
            InlineKeyboardButton(text="📥 Получить дайджест", callback_data="generate_digest"),
            InlineKeyboardButton(text="➕ Добавить источник", callback_data="add_source")
        ])
        
        # Кнопки для действий с конкретными источниками
        for source in sources_list:
            username = source.get('username', '')
            button_text = f"@{username}"
            if source.get('name') and source['name'] != username:
                button_text += f" ({source['name']})"
            
            # Добавляем кнопки для просмотра и удаления источника
            buttons.append([
                InlineKeyboardButton(
                    text=f"📰 {button_text}",
                    url=f"https://t.me/{username}"
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=f"remove_source:{username}"
                )
            ])
        
        markup = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        # Сортируем источники по имени для отображения
        sources_list.sort(key=lambda x: x.get('name', '').lower())
        
        # Создаем сообщение со списком источников
        message_text = "📋 <b>Ваши источники новостей</b>:\n\n"
        
        for i, source in enumerate(sources_list, 1):
            username = source.get('username', '')
            name = source.get('name', username)
            
            if name == username:
                message_text += f"{i}. <a href='https://t.me/{username}'>@{username}</a>\n"
            else:
                message_text += f"{i}. <a href='https://t.me/{username}'>{name}</a> (@{username})\n"
                
        message_text += "\nНажмите на любой источник, чтобы перейти к нему."
                
        try:
            await message.answer(message_text, parse_mode='HTML', reply_markup=markup)
        except Exception as e:
            # Если сообщение слишком длинное или есть другие проблемы с форматированием,
            # отправляем упрощенный список
            logger.error(f"Ошибка при отправке списка источников: {e}")
            await self._show_simple_sources_list(message, sources_list)

    async def _show_simple_sources_list(self, message, sources_list):
        """Отображение упрощенного списка источников без разметки и кнопок"""
        message_text = "📋 Ваши источники новостей:\n\n"
        
        for i, source in enumerate(sources_list, 1):
            username = source.get('username', '')
            name = source.get('name', username)
            
            if name == username:
                message_text += f"{i}. @{username}\n"
            else:
                message_text += f"{i}. {name} (@{username})\n"
                
        message_text += "\nИспользуйте команду /remove_source @username для удаления источника."
        
        await message.answer(message_text)

    async def _check_sources(self, message) -> bool:
        """Проверка наличия источников у пользователя"""
        user_id = message.from_user.id
        
        # Сначала проверим, загружены ли источники
        await self.news_aggregator.ensure_sources_loaded(user_id)
        
        # Затем получаем источники
        sources = await self.news_aggregator.get_sources_async(user_id)
        
        if not sources:
            await message.answer(
                "❌ У вас нет добавленных источников новостей.\n\n"
                "Используйте команду /add_source, чтобы добавить новый источник."
            )
            return False
            
        return True

    async def search_source(self, message: Message, command: CommandObject = None):
        """Поиск источника по названию или имени пользователя"""
        # Получаем ID пользователя
        user_id = message.from_user.id
        
        if not command or not command.args:
            await message.answer(
                "❌ Пожалуйста, укажите строку для поиска.\n"
                "Пример: /search_source economy"
            )
            return
            
        query = command.args.lower()
        
        # Проверяем наличие источников
        sources = await self.news_aggregator.get_source_details_async(user_id)
        
        if not sources:
            await message.answer(
                "📋 У вас нет добавленных источников новостей.\n\n"
                "Используйте команду /add_source, чтобы добавить новый источник."
            )
            return
        
        # Фильтруем источники по запросу
        found_sources = []
        for source in sources:
            username = source.get('username', '').lower()
            name = source.get('name', '').lower()
            
            if query in username or query in name:
                found_sources.append(source)
        
        if not found_sources:
            await message.answer(
                f"🔍 По запросу '{query}' ничего не найдено.\n"
                f"Попробуйте другой запрос или просмотрите полный список источников: /list_sources"
            )
            return
        
        # Формируем сообщение с найденными источниками
        sources_list = []
        for i, source in enumerate(found_sources, 1):
            username = source.get('username', '')
            name = source.get('name', username)
            
            # Если имя и username совпадают, показываем только username
            if name == username:
                sources_list.append(f"{i}. @{username}")
            else:
                sources_list.append(f"{i}. {name} (@{username})")
        
        sources_text = "\n".join(sources_list)
        
        await message.answer(
            f"🔍 Результаты поиска по запросу '{query}' ({len(found_sources)}):\n\n"
            f"{sources_text}\n\n"
            f"Для удаления: /remove_source [username_канала]"
        )

    async def import_sources(self, message: Message, command: CommandObject = None):
        """Импорт источников из файла JSON"""
        # Получаем ID пользователя
        user_id = message.from_user.id
        
        # Проверяем, есть ли у бота доступ к базе данных
        if not hasattr(self.news_aggregator, 'db_manager') or self.news_aggregator.db_manager is None:
            await message.answer(
                "❌ Нет подключения к базе данных. Импорт невозможен."
            )
            return
        
        # Проверяем наличие файла с именем в сообщении
        if not command or not command.args:
            await message.answer(
                "❌ Пожалуйста, укажите имя JSON-файла для импорта.\n"
                "Пример: /import_sources my_sources.json"
            )
            return
            
        json_file = command.args
        
        # Пытаемся импортировать из JSON
        try:
            # Проверяем существование файла
            if not os.path.exists(json_file):
                await message.answer(
                    f"❌ Файл {json_file} не найден."
                )
                return
                
            # Загружаем JSON
            with open(json_file, 'r', encoding='utf-8') as file:
                channels = json.load(file)
            
            # Импортируем в базу данных для конкретного пользователя
            added_count = self.news_aggregator.db_manager.import_from_json(channels, user_id)
            
            # Обновляем источники для пользователя
            await self.news_aggregator._load_sources_for_user_async(user_id)
            
            await message.answer(
                f"✅ Импорт успешно выполнен!\n"
                f"Добавлено {added_count} источников из {len(channels)}.\n\n"
                f"Для просмотра списка источников: /list_sources"
            )
        except Exception as e:
            logger.error(f"Ошибка при импорте источников: {e}")
            await message.answer(
                f"❌ Произошла ошибка при импорте источников: {str(e)}"
            )

    async def styles(self, message: Message):
        """Отображает список доступных стилей дайджеста"""
        # Создаем список стилей с описаниями и эмодзи
        style_emojis = {
            DigestStyle.STANDARD: "📰",
            DigestStyle.COMPACT: "📝",
            DigestStyle.MEDIA: "📱",
            DigestStyle.CARDS: "🗂️",
            DigestStyle.ANALYTICS: "📊",
            DigestStyle.SOCIAL: "📣"
        }
        
        styles_list = []
        for style in DigestStyle:
            emoji = style_emojis.get(style, "🔹")
            description = self._get_style_description(style)
            styles_list.append(f"{emoji} <b>{style.value}</b> - {description}")
        
        styles_text = "\n".join(styles_list)
        
        # Создаем инлайн клавиатуру для быстрого выбора стиля
        keyboard = []
        for style in DigestStyle:
            keyboard.append([InlineKeyboardButton(text=f"{style_emojis.get(style, '🔹')} {style.value}", callback_data=f"style_{style.value}")])
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await message.answer(
            f"🎨 <b>Доступные стили дайджеста</b>:\n\n"
            f"{styles_text}\n\n"
            f"<b>Текущий стиль</b>: {self.current_style.value}\n\n"
            f"Для установки стиля используйте команду /set_style [название_стиля] или нажмите на кнопку ниже:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        
    def _get_style_description(self, style: DigestStyle) -> str:
        """Возвращает описание стиля дайджеста"""
        descriptions = {
            DigestStyle.STANDARD: "Стандартный формат с группировкой новостей по категориям",
            DigestStyle.COMPACT: "Компактный формат для быстрого чтения, минимум текста",
            DigestStyle.MEDIA: "Медиа-ориентированный формат с акцентом на визуальный контент",
            DigestStyle.CARDS: "Каждая новость оформляется как отдельная карточка",
            DigestStyle.ANALYTICS: "Формат с фокусом на анализ данных и экономические тренды",
            DigestStyle.SOCIAL: "Стиль для социальных сетей с хештегами и краткими формулировками"
        }
        return descriptions.get(style, "Стиль без описания")

    async def set_count(self, message: Message, command: CommandObject = None):
        """Установка количества новостей в дайджесте"""
        if not command or not command.args:
            await message.answer(
                "❌ Пожалуйста, укажите количество новостей (1-20).\n"
                "Пример:\n"
                "/set_count 5"
            )
            return
            
        try:
            count = int(command.args)
            if 1 <= count <= 20:
                self.news_count = count
                await message.answer(
                    f"✅ Количество новостей в дайджесте установлено: {count}\n"
                    f"Теперь бот будет собирать {count} самых важных новостей."
                )
            else:
                await message.answer(
                    "❌ Количество новостей должно быть от 1 до 20.\n"
                    "Пожалуйста, введите корректное число."
                )
        except ValueError:
            await message.answer(
                "❌ Пожалуйста, введите корректное число.\n"
                "Пример:\n"
                "/set_count 5"
            )
            
    async def set_frequency(self, message: Message, command: CommandObject = None):
        """Установка частоты обновления дайджеста"""
        if not command or not command.args:
            await message.answer(
                "❌ Пожалуйста, укажите частоту публикации в часах (1-48).\n"
                "Пример:\n"
                "/set_frequency 24"
            )
            return
            
        try:
            hours = int(command.args)
            if 1 <= hours <= 48:
                self.update_frequency = hours
                await message.answer(
                    f"✅ Частота публикации установлена: каждые {hours} часов.\n"
                    f"Бот будет автоматически отправлять дайджест каждые {hours} часов."
                )
                
                # Перезапускаем запланированные задачи
                await self._reschedule_jobs(message)
            else:
                await message.answer(
                    "❌ Частота должна быть от 1 до 48 часов.\n"
                    "Пожалуйста, введите корректное число."
                )
        except ValueError:
            await message.answer(
                "❌ Пожалуйста, введите корректное число.\n"
                "Пример:\n"
                "/set_frequency 24"
            )

    async def _check_sources(self, message) -> bool:
        """Проверка наличия источников у пользователя"""
        user_id = message.from_user.id
        
        # Сначала проверим, загружены ли источники
        await self.news_aggregator.ensure_sources_loaded(user_id)
        
        # Затем получаем источники
        sources = await self.news_aggregator.get_sources_async(user_id)
        
        if not sources:
            await message.answer(
                "❌ У вас нет добавленных источников новостей.\n\n"
                "Используйте команду /add_source, чтобы добавить новый источник."
            )
            return False
            
        return True

    async def generate_digest(self, message: Message):
        """Генерация дайджеста новостей"""
        # Проверяем наличие источников
        if not await self._check_sources(message):
            return
            
        # Получаем идентификатор пользователя
        user_id = message.from_user.id
            
        # Отправляем сообщение о начале сбора новостей
        status_msg = await message.answer("⏳ Начинаю сбор и анализ новостей...")
        
        # Запускаем процесс генерации дайджеста в фоне для избежания таймаута Telegram
        task = asyncio.create_task(self._generate_digest_background(
            chat_id=message.chat.id, 
            status_msg_id=status_msg.message_id,
            user_id=user_id
        ))
        # Сохраняем задачу, чтобы она не была собрана сборщиком мусора
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _generate_digest_background(self, chat_id, status_msg_id, user_id):
        """Фоновая генерация дайджеста новостей"""
        try:
            # Обновляем статус
            await self.bot.edit_message_text(
                "⏳ Собираю последние новости из всех источников...",
                chat_id=chat_id,
                message_id=status_msg_id
            )
            
            # Получаем последние новости из всех источников
            start_time = datetime.now()
            news_list = await self.news_aggregator.get_latest_news(
                hours=self.update_frequency,
                user_id=user_id
            )
            
            # Логируем настройки в фоновом процессе
            logger.info(f"Фоновая генерация: news_count={self.news_count}, style={self.current_style.value}, include_analysis={self.include_analysis}")
            
            # Добавляем задержку перед началом обработки
            await asyncio.sleep(1)
            
            # Получаем список источников
            sources = list(self.news_aggregator.get_sources(user_id))
            if not sources:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text="⚠️ Список источников новостей пуст! Добавьте источники с помощью /add_source.",
                    parse_mode=ParseMode.HTML
                )
                return
            
            # Обновляем статусное сообщение
            await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=f"⏳ Сбор новостей из {len(sources)} каналов...",
                parse_mode=ParseMode.HTML
            )
            
            # Сначала получаем все новости
            try:
                all_news = await self.news_aggregator.get_latest_news(hours=24, user_id=user_id)
                logger.info(f"Получено всего {len(all_news)} новостей из всех источников")
            except Exception as e:
                logger.error(f"Ошибка при получении новостей: {e}")
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text="❌ Произошла ошибка при получении новостей. Попробуйте позже.",
                    parse_mode=ParseMode.HTML
                )
                return
            
            # Группируем новости по источникам
            news_by_source = {}
            for news_item in all_news:
                source = news_item.get('source')
                if source:
                    if source not in news_by_source:
                        news_by_source[source] = []
                    news_by_source[source].append(news_item)
            
            # Показываем статистику для информирования пользователя
            for i, source in enumerate(sources):
                # Обновляем статус для информирования пользователя
                if len(sources) > 3 and i > 0 and i % 2 == 0:
                    try:
                        source_news_count = len(news_by_source.get(source, []))
                        await self.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=status_msg_id,
                            text=f"⏳ Анализ новостей: канал {i+1}/{len(sources)} - @{source} ({source_news_count} новостей)",
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as e:
                        logger.error(f"Ошибка при обновлении статуса: {e}")
                
                # Добавляем небольшую задержку между обновлениями
                if i < len(sources) - 1:  # Не ждем после последнего источника
                    await asyncio.sleep(0.3)
            
            # Если новостей нет, сообщаем об этом
            if not all_news:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text="ℹ️ Не найдено новостей за последние 24 часа.",
                    parse_mode=ParseMode.HTML
                )
                return
                
            await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=f"⏳ Обработка {len(all_news)} новостей...\n"
                     f"Удаление дубликатов и ранжирование...",
                parse_mode=ParseMode.HTML
            )
                    
            # Удаляем дубликаты и ранжируем новости
            news = self.news_aggregator.remove_duplicates(all_news)
            news = self.news_aggregator.rank_news(news)
            
            # Ограничиваем количество новостей
            news = news[:self.news_count]
            logger.info(f"После ограничения: количество новостей={len(news)}, установленный лимит={self.news_count}")
            
            # Если после фильтрации новостей не осталось, сообщаем об этом
            if not news:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text="ℹ️ После фильтрации дубликатов не осталось подходящих новостей.",
                    parse_mode=ParseMode.HTML
                )
                return
                    
            # Анализируем новости с помощью нового генератора - АСИНХРОННО
            await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=f"⏳ Генерирую дайджест из {len(news)} новостей...\n"
                     f"Анализ и суммаризация информации...",
                parse_mode=ParseMode.HTML
            )
            
            # Обрабатываем новости последовательно с задержками для избежания лимитов API
            analyzed_news = []
            logger.info(f"Начинаем обработку {len(news)} новостей, лимит={self.news_count}")
            for i, news_item in enumerate(news):
                # Обновляем статус обработки для длинных списков новостей
                if len(news) > 3 and i > 0 and i % 2 == 0:
                    try:
                        await self.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=status_msg_id,
                            text=f"⏳ Генерирую дайджест из {len(news)} новостей...\n"
                                f"Обработано: {i}/{len(news)} новостей",
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as e:
                        # Игнорируем ошибки при обновлении сообщения
                        logger.error(f"Ошибка при обновлении статуса: {e}")
                
                # Извлекаем текст новости
                raw_text = news_item['text']
                # Анализируем новость
                analyzed = await self._analyze_news_item(raw_text, news_item.get('url'))
                if analyzed:
                    analyzed_news.append(analyzed)
                
                # Добавляем задержку между обработкой новостей для избежания лимитов API
                if i < len(news) - 1:  # Не ждем после последней новости
                    await asyncio.sleep(0.5)
            
            # Если не удалось проанализировать ни одной новости
            if not analyzed_news:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text="❌ Не удалось проанализировать новости. Попробуйте позже.",
                    parse_mode=ParseMode.HTML
                )
                return
            
            # Получаем общий анализ всех новостей
            overall_analysis = None
            if self.include_analysis:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text=f"⏳ Выполняю анализ тенденций на основе {len(analyzed_news)} новостей...",
                    parse_mode=ParseMode.HTML
                )
                
                # Добавляем задержку перед анализом для избежания лимитов API
                await asyncio.sleep(2)
                
                try:
                    overall_analysis = await asyncio.wait_for(
                        self._generate_overall_analysis(analyzed_news),
                        timeout=90  # Увеличиваем таймаут для анализа
                    )
                except asyncio.TimeoutError:
                    logger.error("Превышен таймаут при генерации общего анализа")
                except Exception as e:
                    logger.error(f"Ошибка при генерации общего анализа: {e}")
            
            # Генерируем финальный дайджест
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text=f"⏳ Формирую итоговый дайджест...",
                    parse_mode=ParseMode.HTML
                )
                
                # Номер дайджеста (можно улучшить, добавив учет уже созданных дайджестов)
                digest_number = datetime.now().strftime("%Y%m%d%H")
                
                # Генерируем дайджест с использованием DigestGenerator
                summary = self.digest_generator.generate_digest(
                    analyzed_news=analyzed_news,
                    digest_number=int(digest_number) % 100,  # Для краткости берем остаток от деления
                    style=self.current_style
                )
                
                # Если есть анализ тренда и он включен, добавляем его в конец
                if overall_analysis and self.include_analysis:
                    summary += f"\n\n<b>📊 АНАЛИЗ ТЕНДЕНЦИЙ:</b>\n{overall_analysis}\n\n"
                elif "📊 АНАЛИЗ ТЕНДЕНЦИЙ:" in summary and not self.include_analysis:
                    # Если анализ тенденций отключен, но он присутствует в шаблоне дайджеста,
                    # удаляем его из итогового сообщения
                    pattern = r'\n*<b>📊 АНАЛИЗ ТЕНДЕНЦИЙ:</b>\n.*?\n\n'
                    summary = re.sub(pattern, '\n\n', summary, flags=re.DOTALL)
                
                # Добавляем информацию об источниках в конец дайджеста
                sources_used = set()
                for news_item in analyzed_news:
                    if 'source' in news_item:
                        sources_used.add(news_item['source'])
                
                if sources_used:
                    sources_list = ", ".join([f"@{source}" for source in sorted(sources_used)])
                    summary += f"\n\n<i>Источники: {sources_list}</i>"
                
                # Удаляем промежуточное сообщение
                await self.bot.delete_message(chat_id=chat_id, message_id=status_msg_id)
                
                # Проверяем длину сообщения и при необходимости разбиваем на части
                if len(summary) > 4000:
                    # Разбиваем на части по 4000 символов
                    parts = [summary[i:i+4000] for i in range(0, len(summary), 4000)]
                    
                    # Отправляем части одну за другой
                    for i, part in enumerate(parts):
                        # Добавляем пометку о части для длинных дайджестов
                        if len(parts) > 1:
                            part_header = f"<b>Часть {i+1}/{len(parts)}</b>\n\n"
                            if i > 0:  # Для всех частей кроме первой
                                part = part_header + part
                        
                        # Отправляем сообщение
                        await self.bot.send_message(
                            chat_id=chat_id,
                            text=part,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=False
                        )
                else:
                    # Отправляем дайджест одним сообщением
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=summary,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False
                    )
            except Exception as e:
                logger.error(f"Ошибка при генерации итогового дайджеста: {e}")
                await self.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Произошла ошибка при создании дайджеста. Попробуйте позже.",
                    parse_mode=ParseMode.HTML
                )
                
        except Exception as e:
            logger.error(f"Ошибка при генерации дайджеста: {e}")
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Произошла ошибка при генерации дайджеста.\n"
                         "Пожалуйста, попробуйте позже.",
                    parse_mode=ParseMode.HTML
                )
            except Exception as send_error:
                logger.error(f"Не удалось отправить сообщение об ошибке: {send_error}")
    
    async def _analyze_news_item(self, raw_text, url=None):
        """Асинхронный анализ отдельной новости"""
        try:
            # Добавляем случайную задержку от 0.5 до 1.5 секунды для избежания лимитов API
            await asyncio.sleep(0.5 + random.random())
            
            # Используем asyncio.to_thread для запуска в отдельном потоке
            # это позволит освободить event loop для обработки других запросов
            loop = asyncio.get_running_loop()
            
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
            logger.error(f"Ошибка при анализе новости: {e}")
            return None
    
    async def _generate_overall_analysis(self, analyzed_news):
        """Асинхронная генерация общего анализа новостей"""
        try:
            # Запускаем тяжелую операцию в отдельном потоке
            loop = asyncio.get_running_loop()
            
            return await loop.run_in_executor(
                None,  # использовать default executor
                lambda: self.news_analyzer.generate_overall_analysis(
                    analyzed_news, 
                    style=self.current_style
                )
            )
        except Exception as e:
            logger.error(f"Ошибка при генерации общего анализа: {e}")
            return None
    
    async def _reschedule_jobs(self, message):
        """Перезапускает планировщик заданий с новой частотой"""
        # Здесь должна быть реализация планировщика заданий
        # В aiogram 3 можно использовать библиотеку APScheduler
        # и настроить периодическую отправку дайджестов
        # Это не было реализовано в оригинальном коде
        pass

    async def _send_message_safe(self, message, text, parse_mode=None):
        """Безопасная отправка сообщений с обработкой ошибок"""
        try:
            return await message.answer(
                text=text,
                parse_mode=parse_mode,
                disable_web_page_preview=False
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения: {e}")
            # Пробуем отправить без HTML форматирования в случае ошибки
            if parse_mode == 'HTML':
                plain_text = self._strip_html_tags(text)
                return await message.answer(
                    text=plain_text,
                    disable_web_page_preview=False
                )
            else:
                raise

    def _strip_html_tags(self, text):
        """Удаление HTML-тегов из текста"""
        return re.sub(r'<[^>]+>', '', text)

    async def set_style(self, message: Message, command: CommandObject = None):
        """Установка стиля дайджеста"""
        if not message:
            logger.error("Ошибка в set_style: message отсутствует")
            return
           
        # Проверяем наличие аргументов команды
        if command and command.args:
            style_name = command.args.lower()
            try:
                new_style = DigestStyle(style_name)
                self.current_style = new_style
                 
                # Получаем описание стиля
                style_description = self._get_style_description(new_style)
                 
                await message.answer(
                    f"✅ Стиль дайджеста изменен на <b>{new_style.value}</b>.\n\n"
                    f"<i>{style_description}</i>",
                    parse_mode='HTML'
                )
                     
            except ValueError:
                # Если стиль не существует
                available_styles = [style.value for style in DigestStyle]
                styles_text = ", ".join(available_styles)
                 
                await message.answer(f"❌ Неизвестный стиль '{style_name}'.\nДоступные стили: {styles_text}")
            return
           
        # Если стиль не указан, показываем доступные стили
        available_styles = [style.value for style in DigestStyle]
        styles_text = "\n".join([f"• {style}" for style in available_styles])
           
        # Создаем инлайн клавиатуру для выбора стиля
        keyboard = []
        style_emojis = {
            DigestStyle.STANDARD: "📰",
            DigestStyle.COMPACT: "📝",
            DigestStyle.MEDIA: "📱",
            DigestStyle.CARDS: "🗂️",
            DigestStyle.ANALYTICS: "📊",
            DigestStyle.SOCIAL: "📣"
        }
        for style in DigestStyle:
            keyboard.append([InlineKeyboardButton(
                text=f"{style_emojis.get(style, '🔹')} {style.value}", 
                callback_data=f"style_{style.value}"
            )])
           
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
           
        # Формируем сообщение с описаниями стилей
        styles_desc = []
        for style in DigestStyle:
            emoji = style_emojis.get(style, "")
            desc = self._get_style_description(style)
            styles_desc.append(f"{emoji} <b>{style.value}</b>: {desc}")
           
        styles_text = "\n\n".join(styles_desc)
           
        await message.answer(
            "🎨 <b>Доступные стили дайджеста</b>\n\n"
            f"{styles_text}\n\n"
            f"Текущий стиль: <b>{self.current_style.value}</b>",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

    async def toggle_analysis(self, message: Message):
        """Включение/выключение анализа трендов в дайджесте"""
        self.include_analysis = not self.include_analysis
        status = "включен" if self.include_analysis else "отключен"
        
        await message.answer(
            f"✅ Анализ трендов в дайджесте {status}.\n\n"
            f"{'🔍 Теперь бот будет анализировать тенденции в новостях и добавлять аналитику в дайджест.' if self.include_analysis else '📰 Теперь бот будет создавать дайджест без аналитики трендов.'}"
        )

    async def settings(self, message: Message):
        """Меню настроек бота"""
        # Создаем инлайн-клавиатуру с настройками
        keyboard = [
            [
                InlineKeyboardButton(
                    text=f"📊 {'Выключить' if self.include_analysis else 'Включить'} анализ трендов",
                    callback_data="toggle_analysis"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔢 Изменить количество новостей",
                    callback_data="set_count_menu"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🕒 Изменить частоту обновления",
                    callback_data="set_frequency_menu"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎨 Изменить стиль дайджеста",
                    callback_data="set_style_menu"
                )
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await message.answer(
            f"⚙️ <b>Настройки бота</b>\n\n"
            f"Текущие параметры:\n"
            f"• Количество новостей: <b>{self.news_count}</b>\n"
            f"• Частота обновления: <b>каждые {self.update_frequency} часов</b>\n"
            f"• Стиль дайджеста: <b>{self.current_style.value}</b>\n"
            f"• Анализ трендов: <b>{'включен' if self.include_analysis else 'отключен'}</b>\n\n"
            f"Выберите параметр для изменения:",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

    async def publish_to_channel(self, message: Message, command: CommandObject = None):
        """Публикация дайджеста в указанный канал"""
        if not command or not command.args:
            await message.answer(
                "❌ Пожалуйста, укажите имя канала для публикации.\n"
                "Пример: /publish_to_channel @my_channel\n\n"
                "⚠️ Бот должен быть администратором канала с правом публикации сообщений."
            )
            return
            
        channel = command.args
        
        # Проверяем формат канала
        if not (channel.startswith('@') or 't.me/' in channel):
            await message.answer(
                "❌ Неверный формат канала. Укажите имя в формате @channel_name или ссылку https://t.me/channel_name"
            )
            return
            
        # Очищаем имя канала
        clean_channel = channel
        if 't.me/' in channel:
            clean_channel = channel.split('t.me/')[-1].split('/')[0]
        elif channel.startswith('@'):
            clean_channel = channel[1:]
            
        # Проверяем наличие источников
        if not await self._check_sources(message):
            return
            
        # Проверяем права бота в канале
        try:
            # Пробуем получить информацию о канале
            chat_member = await self.bot.get_chat_member(f"@{clean_channel}", self.bot.id)
            
            # Проверяем, является ли бот администратором
            if not chat_member.status in ['administrator', 'creator']:
                await message.answer(
                    f"❌ Бот не является администратором канала @{clean_channel}.\n"
                    f"Пожалуйста, добавьте бота как администратора с правом отправки сообщений."
                )
                return
        except Exception as e:
            logger.error(f"Ошибка при проверке прав бота в канале {clean_channel}: {e}")
            await message.answer(
                f"❌ Не удалось получить информацию о канале @{clean_channel}.\n"
                f"Убедитесь, что канал существует и бот является его администратором."
            )
            return
            
        # Генерируем и публикуем дайджест
        status_msg = await message.answer(f"⏳ Генерирую дайджест для публикации в @{clean_channel}...")
        
        try:
            # Логика генерации дайджеста, аналогичная методу generate_digest
            # Получаем новости
            news = await self.news_aggregator.get_latest_news(hours=24, user_id=message.from_user.id)
            
            if not news:
                await status_msg.edit_text(
                    f"ℹ️ Не найдено новостей за последние 24 часа. Публикация отменена."
                )
                return
                
            await status_msg.edit_text(
                f"⏳ Обработка {len(news)} новостей для канала @{clean_channel}..."
            )
                
            # Удаляем дубликаты и ранжируем
            news = self.news_aggregator.remove_duplicates(news)
            news = self.news_aggregator.rank_news(news)
            news = news[:self.news_count]
            
            if not news:
                await status_msg.edit_text(
                    f"ℹ️ После фильтрации не осталось подходящих новостей. Публикация отменена."
                )
                return
                
            # Анализируем новости
            await status_msg.edit_text(
                f"⏳ Генерирую дайджест из {len(news)} новостей для канала @{clean_channel}..."
            )
            
            # Анализируем каждую новость
            analysis_tasks = []
            for news_item in news:
                task = asyncio.create_task(self._analyze_news_item(news_item['text'], news_item.get('url')))
                analysis_tasks.append(task)
                
            analyzed_news_results = await asyncio.gather(*analysis_tasks)
            analyzed_news = [result for result in analyzed_news_results if result is not None]
            
            if not analyzed_news:
                await status_msg.edit_text(
                    f"❌ Не удалось проанализировать новости. Публикация отменена."
                )
                return
                
            # Получаем общий анализ
            overall_analysis = None
            if self.include_analysis:
                try:
                    overall_analysis = await asyncio.wait_for(
                        self._generate_overall_analysis(analyzed_news),
                        timeout=60
                    )
                except Exception as e:
                    logger.error(f"Ошибка при генерации анализа для публикации: {e}")
                    
            # Генерируем финальный дайджест
            digest_number = datetime.now().strftime("%Y%m%d%H")
            
            summary = self.digest_generator.generate_digest(
                analyzed_news=analyzed_news,
                digest_number=int(digest_number) % 100,
                style=self.current_style
            )
            
            if overall_analysis and self.include_analysis:
                summary += f"\n\n<b>📊 АНАЛИЗ ТЕНДЕНЦИЙ:</b>\n{overall_analysis}\n\n"
                
            # Публикуем дайджест в канал
            await status_msg.edit_text(
                f"⏳ Публикация дайджеста в канал @{clean_channel}..."
            )
            
            # Если дайджест слишком длинный, разбиваем на части
            if len(summary) > 4000:
                parts = [summary[i:i+4000] for i in range(0, len(summary), 4000)]
                
                for i, part in enumerate(parts):
                    if len(parts) > 1:
                        part_header = f"<b>Часть {i+1}/{len(parts)}</b>\n\n"
                        if i > 0:
                            part = part_header + part
                            
                    await self.bot.send_message(
                        chat_id=f"@{clean_channel}",
                        text=part,
                        parse_mode='HTML',
                        disable_web_page_preview=False
                    )
            else:
                await self.bot.send_message(
                    chat_id=f"@{clean_channel}",
                    text=summary,
                    parse_mode='HTML',
                    disable_web_page_preview=False
                )
                
            await status_msg.edit_text(
                f"✅ Дайджест успешно опубликован в канале @{clean_channel}!"
            )
            
        except Exception as e:
            logger.error(f"Ошибка при публикации дайджеста: {e}")
            await status_msg.edit_text(
                f"❌ Произошла ошибка при публикации дайджеста: {str(e)}"
            )

    async def button_callback(self, callback_query: CallbackQuery, state: FSMContext):
        """Обработка callback-запросов от кнопок"""
        # Получаем данные из callback
        data = callback_query.data
        user_id = callback_query.from_user.id
        
        # Обрабатываем различные типы callback-запросов
        if data == "generate_digest":
            # Генерируем дайджест
            await callback_query.answer("Начинаю генерацию дайджеста...")
            await self.generate_digest(callback_query.message)
            
        elif data == "add_source":
            # Переводим пользователя в режим ожидания ввода источника
            await callback_query.answer("Укажите Telegram канал для добавления")
            await state.set_state(UserStates.waiting_for_source)
            await callback_query.message.answer(
                "🔍 Пожалуйста, введите имя Telegram канала для добавления.\n\n"
                "Например: @channelname или https://t.me/channelname"
            )
            
        elif data.startswith("remove_source:"):
            # Удаляем источник
            source = data.split(":", 1)[1]
            await callback_query.answer(f"Удаляю источник @{source}...")
            
            # Проверяем, действительно ли у пользователя есть этот источник
            user_sources = await self.news_aggregator.get_sources_async(user_id)
            if source in user_sources:
                result = await self.news_aggregator.remove_source_async(source, user_id)
                if result:
                    await callback_query.message.reply(f"✅ Источник @{source} успешно удален!")
                    
                    # Обновляем список источников, если это был запрос из списка
                    if "Ваши источники новостей" in callback_query.message.text:
                        await self.list_sources(callback_query.message)
                else:
                    await callback_query.message.reply(f"❌ Не удалось удалить источник @{source}")
            else:
                await callback_query.message.reply(f"⚠️ Источник @{source} не найден в вашем списке.")
                
        elif data.startswith("from_source:"):
            # Генерация дайджеста из выбранного источника
            source = data.split(":", 1)[1]
            await callback_query.answer(f"Генерирую дайджест из @{source}...")
            
            # Проверяем, действительно ли у пользователя есть этот источник
            user_sources = await self.news_aggregator.get_sources_async(user_id)
            if source in user_sources:
                # Отправляем сообщение о начале сбора новостей
                status_msg = await callback_query.message.reply(f"⏳ Собираю новости из @{source}...")
                
                # Запускаем процесс генерации дайджеста в фоне
                task = asyncio.create_task(self._generate_from_source_background(
                    chat_id=callback_query.message.chat.id,
                    status_msg_id=status_msg.message_id,
                    source=source,
                    user_id=user_id
                ))
                # Сохраняем задачу, чтобы она не была собрана сборщиком мусора
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
            else:
                await callback_query.message.reply(f"⚠️ Источник @{source} не найден в вашем списке.")

    async def add_source_menu(self, message: Message, state: FSMContext):
        """Меню добавления источника"""
        await state.set_state(UserStates.waiting_for_source)
        await message.answer(
            "➕ Введите имя канала для добавления в формате @username или https://t.me/username"
        )

    async def process_text_input(self, message: Message, state: FSMContext):
        """Обработка текстовых сообщений пользователя"""
        # Получаем текущее состояние пользователя
        current_state = await state.get_state()
        user_id = message.from_user.id
        
        # Обрабатываем различные состояния
        if current_state == UserStates.waiting_for_count.state:
            # Пользователь указывает количество новостей
            try:
                count = int(message.text.strip())
                if count < 1 or count > 50:
                    await message.answer("❌ Количество новостей должно быть в диапазоне от 1 до 50. Пожалуйста, введите корректное значение:")
                    return
                    
                self.news_count = count
                await state.clear()
                await message.answer(f"✅ Количество новостей установлено: {count}", reply_markup=self.main_keyboard)
                
            except ValueError:
                await message.answer("❌ Пожалуйста, введите число от 1 до 50:")
                
        elif current_state == UserStates.waiting_for_frequency.state:
            # Пользователь указывает частоту обновления
            try:
                hours = int(message.text.strip())
                if hours < 1 or hours > 168:  # Максимум неделя (7 * 24 = 168 часов)
                    await message.answer("❌ Частота обновления должна быть в диапазоне от 1 до 168 часов. Пожалуйста, введите корректное значение:")
                    return
                    
                self.update_frequency = hours
                await state.clear()
                await message.answer(f"✅ Частота обновления установлена: {hours} ч.", reply_markup=self.main_keyboard)
                
                # Обновляем расписание заданий
                await self._reschedule_jobs(message)
                
            except ValueError:
                await message.answer("❌ Пожалуйста, введите число от 1 до 168:")
                
        elif current_state == UserStates.waiting_for_source.state:
            # Пользователь указывает источник для добавления
            channel = message.text.strip()
            
            # Очищаем имя канала от URL и @
            clean_channel = channel
            if 't.me/' in channel:
                clean_channel = channel.split('t.me/')[-1].split('/')[0]
            elif channel.startswith('@'):
                clean_channel = channel[1:]
                
            # Валидация формата имени канала
            if not re.match(r'^[a-zA-Z0-9_]+$', clean_channel):
                await message.answer(
                    f"❌ Неверный формат имени канала: {channel}\n"
                    f"Имя канала должно содержать только латинские буквы, цифры и символ подчеркивания.\n\n"
                    f"Попробуйте еще раз:"
                )
                return
                
            # Сбрасываем состояние пользователя
            await state.clear()
            
            # Отправляем сообщение о начале проверки канала
            status_msg = await message.answer(f"⏳ Проверяю доступность канала @{clean_channel}...")
            
            # Проверяем, можем ли мы получить доступ к каналу
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"https://t.me/s/{clean_channel}"
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
                    
                    async with session.get(url, headers=headers, timeout=10) as response:
                        if response.status != 200:
                            await status_msg.edit_text(
                                f"❌ Не удалось получить доступ к каналу @{clean_channel}.\n"
                                f"Убедитесь, что канал существует и является публичным."
                            )
                            return
                        
                        # Проверяем, что страница содержит сообщения канала
                        html = await response.text()
                        if "tgme_widget_message" not in html:
                            await status_msg.edit_text(
                                f"❌ Канал @{clean_channel} не содержит сообщений или является приватным."
                            )
                            return
                        
                        # Канал доступен, добавляем его
                        result = await self.news_aggregator.add_source_async(clean_channel, user_id)
                        
                        if result:
                            await status_msg.edit_text(
                                f"✅ Канал @{clean_channel} успешно добавлен в ваши источники новостей!"
                            )
                            # Предлагаем сгенерировать дайджест
                            markup = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="📰 Создать дайджест", callback_data="generate_digest")]
                            ])
                            await message.answer(
                                "Хотите сразу создать дайджест с новым источником?",
                                reply_markup=markup
                            )
                        else:
                            await status_msg.edit_text(
                                f"⚠️ Канал @{clean_channel} уже есть в вашем списке источников."
                            )
            except Exception as e:
                logger.error(f"Ошибка при добавлении источника: {e}")
                await status_msg.edit_text(
                    f"❌ Произошла ошибка при добавлении канала @{clean_channel}.\n"
                    f"Пожалуйста, попробуйте позже."
                )

    async def run(self):
        """Запуск бота"""
        if not self.token:
            logger.error("Токен бота не найден! Проверьте файл .env")
            return
            
        try:
            # Настраиваем параметры для параллельной обработки сообщений
            logger.info("Запуск бота...")
            
            # Запускаем веб-интерфейс в отдельном потоке, если он инициализирован
            if self.web_module:
                import threading
                
                # Создаем и запускаем поток для веб-интерфейса
                web_thread = threading.Thread(
                    target=self.web_module.run,
                    kwargs={"debug": False},
                    daemon=True  # Поток завершится, когда завершится основная программа
                )
                web_thread.start()
                logger.info(f"Веб-интерфейс запущен на {self.web_module.host}:{self.web_module.port}")
            
            # Создаем планировщик задач
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            scheduler = AsyncIOScheduler()
            
            # Запускаем планировщик
            scheduler.start()
            
            # Запускаем поллинг с настройками для параллельной обработки
            await self.dp.start_polling(
                self.bot,
                polling_timeout=10,  # Уменьшаем таймаут для более быстрого обнаружения новых сообщений
                allowed_updates=[
                    "message",
                    "edited_message",
                    "callback_query",
                    "inline_query"
                ],
                handle_signals=True,
                close_bot_session=True,
                # Увеличиваем количество обрабатываемых сообщений в очереди
                skip_updates=True
            )
        except (KeyboardInterrupt, SystemExit):
            logger.info("Бот остановлен пользователем")
        except Exception as e:
            logger.error(f"Ошибка при запуске бота: {e}")
        finally:
            # Останавливаем бота при завершении
            # Отменяем все фоновые задачи
            for task in self._background_tasks:
                task.cancel()
            
            # Ждем завершения всех задач
            if self._background_tasks:
                await asyncio.wait(self._background_tasks)
                
            await self.bot.session.close()
            
    async def generate_from_source(self, message: Message, command: CommandObject = None):
        """Генерация дайджеста из конкретного источника"""
        user_id = message.from_user.id
        
        if not command or not command.args:
            # Если команда вызвана без аргументов, показываем список источников пользователя
            sources_list = await self.news_aggregator.get_source_details_async(user_id)
            
            if not sources_list:
                await message.answer(
                    "❌ У вас нет добавленных источников новостей.\n\n"
                    "Используйте команду /add_source, чтобы добавить источник."
                )
                return
                
            # Создаем инлайн-кнопки для выбора источника
            buttons = []
            
            for source in sources_list:
                button_text = f"@{source['username']}"
                if source.get('name') and source['name'] != source['username']:
                    button_text += f" ({source['name']})"
                    
                buttons.append([InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"from_source:{source['username']}"
                )])
            
            markup = InlineKeyboardMarkup(inline_keyboard=buttons)
                
            await message.answer(
                "📰 Выберите источник для генерации дайджеста:",
                reply_markup=markup
            )
            return
            
        # Если указан источник, генерируем дайджест из него
        source = command.args
        
        # Очищаем имя источника от URL и @
        clean_source = source
        if 't.me/' in source:
            clean_source = source.split('t.me/')[-1].split('/')[0]
        elif source.startswith('@'):
            clean_source = source[1:]
            
        # Проверяем, есть ли такой источник в списке пользователя
        user_sources = await self.news_aggregator.get_sources_async(user_id)
        if clean_source not in user_sources:
            await message.answer(
                f"❌ Источник @{clean_source} не найден в вашем списке источников.\n\n"
                f"Используйте команду /list_sources, чтобы увидеть список доступных источников."
            )
            return
            
        # Отправляем сообщение о начале сбора новостей
        status_msg = await message.answer(f"⏳ Собираю новости из @{clean_source}...")
        
        # Запускаем процесс генерации дайджеста в фоне
        task = asyncio.create_task(self._generate_from_source_background(
            chat_id=message.chat.id,
            status_msg_id=status_msg.message_id,
            source=clean_source,
            user_id=user_id
        ))
        # Сохраняем задачу, чтобы она не была собрана сборщиком мусора
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _generate_from_source_background(self, chat_id, status_msg_id, source, user_id):
        """Фоновая генерация дайджеста из конкретного источника"""
        try:
            # Логируем настройки в фоновом процессе
            logger.info(f"Фоновая генерация из источника @{source}: news_count={self.news_count}, style={self.current_style.value}")
            
            # Добавляем задержку перед началом обработки
            await asyncio.sleep(1)
            
            # Обновляем статусное сообщение
            await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=f"⏳ Сбор новостей из канала @{source}...",
                parse_mode=ParseMode.HTML
            )
            
            # Получаем все новости, а затем фильтруем по источнику
            try:
                # Получаем все новости
                all_news = await self.news_aggregator.get_latest_news(hours=24, user_id=user_id)
                
                # Фильтруем новости по источнику
                news = [item for item in all_news if item.get('source') == source]
                
                logger.info(f"Получено {len(news)} новостей из канала @{source} (всего новостей: {len(all_news)})")
            except Exception as e:
                logger.error(f"Ошибка при получении новостей: {e}")
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text=f"❌ Произошла ошибка при получении новостей из канала @{source}.",
                    parse_mode=ParseMode.HTML
                )
                return
            
            # Если новостей нет, сообщаем об этом
            if not news:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text=f"ℹ️ Не найдено новостей за последние 24 часа в канале @{source}.",
                    parse_mode=ParseMode.HTML
                )
                return
                
            await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=f"⏳ Обработка {len(news)} новостей из канала @{source}...\n"
                     f"Удаление дубликатов и ранжирование...",
                parse_mode=ParseMode.HTML
            )
                    
            # Удаляем дубликаты и ранжируем новости
            news = self.news_aggregator.remove_duplicates(news)
            news = self.news_aggregator.rank_news(news)
            
            # Ограничиваем количество новостей
            news = news[:self.news_count]
            logger.info(f"После ограничения (канал @{source}): количество новостей={len(news)}, установленный лимит={self.news_count}")
            
            # Если после фильтрации новостей не осталось, сообщаем об этом
            if not news:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text=f"ℹ️ После фильтрации дубликатов не осталось подходящих новостей в канале @{source}.",
                    parse_mode=ParseMode.HTML
                )
                return
                    
            # Анализируем новости с помощью нового генератора - АСИНХРОННО
            await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=f"⏳ Генерирую дайджест из {len(news)} новостей канала @{source}...\n"
                     f"Анализ и суммаризация информации...",
                parse_mode=ParseMode.HTML
            )
            
            # Обрабатываем новости последовательно с задержками для избежания лимитов API
            analyzed_news = []
            logger.info(f"Начинаем обработку {len(news)} новостей, лимит={self.news_count}")
            for i, news_item in enumerate(news):
                # Обновляем статус обработки для длинных списков новостей
                if len(news) > 3 and i > 0 and i % 2 == 0:
                    try:
                        await self.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=status_msg_id,
                            text=f"⏳ Генерирую дайджест из {len(news)} новостей канала @{source}...\n"
                                f"Обработано: {i}/{len(news)} новостей",
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as e:
                        # Игнорируем ошибки при обновлении сообщения
                        logger.error(f"Ошибка при обновлении статуса: {e}")
                
                # Извлекаем текст новости
                raw_text = news_item['text']
                # Анализируем новость
                analyzed = await self._analyze_news_item(raw_text, news_item.get('url'))
                if analyzed:
                    analyzed_news.append(analyzed)
                
                # Добавляем задержку между обработкой новостей для избежания лимитов API
                if i < len(news) - 1:  # Не ждем после последней новости
                    await asyncio.sleep(0.5)
            
            # Если не удалось проанализировать ни одной новости
            if not analyzed_news:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text="❌ Не удалось проанализировать новости. Попробуйте позже.",
                    parse_mode=ParseMode.HTML
                )
                return
            
            # Получаем общий анализ всех новостей
            overall_analysis = None
            if self.include_analysis:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text=f"⏳ Выполняю анализ тенденций на основе {len(analyzed_news)} новостей канала @{source}...",
                    parse_mode=ParseMode.HTML
                )
                
                # Добавляем задержку перед анализом для избежания лимитов API
                await asyncio.sleep(2)
                
                try:
                    overall_analysis = await asyncio.wait_for(
                        self._generate_overall_analysis(analyzed_news),
                        timeout=90  # Увеличиваем таймаут для анализа
                    )
                except asyncio.TimeoutError:
                    logger.error("Превышен таймаут при генерации общего анализа")
                except Exception as e:
                    logger.error(f"Ошибка при генерации общего анализа: {e}")
            else:
                logger.info("Анализ тенденций отключен пользователем, пропускаем этот шаг")
            
            # Генерируем финальный дайджест
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text=f"⏳ Формирую итоговый дайджест канала @{source}...",
                    parse_mode=ParseMode.HTML
                )
                
                # Номер дайджеста (можно улучшить, добавив учет уже созданных дайджестов)
                digest_number = datetime.now().strftime("%Y%m%d%H")
                
                # Генерируем дайджест с использованием DigestGenerator
                summary = self.digest_generator.generate_digest(
                    analyzed_news=analyzed_news,
                    digest_number=int(digest_number) % 100,  # Для краткости берем остаток от деления
                    style=self.current_style
                )
                
                # Если есть анализ тренда и он включен, добавляем его в конец
                if overall_analysis and self.include_analysis:
                    summary += f"\n\n<b>📊 АНАЛИЗ ТЕНДЕНЦИЙ:</b>\n{overall_analysis}\n\n"
                elif "📊 АНАЛИЗ ТЕНДЕНЦИЙ:" in summary and not self.include_analysis:
                    # Если анализ тенденций отключен, но он присутствует в шаблоне дайджеста,
                    # удаляем его из итогового сообщения
                    pattern = r'\n*<b>📊 АНАЛИЗ ТЕНДЕНЦИЙ:</b>\n.*?\n\n'
                    summary = re.sub(pattern, '\n\n', summary, flags=re.DOTALL)
                
                # Добавляем информацию об источниках в конец дайджеста
                sources_used = set()
                for news_item in analyzed_news:
                    if 'source' in news_item:
                        sources_used.add(news_item['source'])
                
                if sources_used:
                    sources_list = ", ".join([f"@{source}" for source in sorted(sources_used)])
                    summary += f"\n\n<i>Источники: {sources_list}</i>"
                
                # Добавляем источник в заголовок дайджеста
                summary = f"<b>📰 ДАЙДЖЕСТ КАНАЛА @{source}</b>\n\n" + summary
                
                # Удаляем промежуточное сообщение
                await self.bot.delete_message(chat_id=chat_id, message_id=status_msg_id)
                
                # Проверяем длину сообщения и при необходимости разбиваем на части
                if len(summary) > 4000:
                    # Разбиваем на части по 4000 символов
                    parts = [summary[i:i+4000] for i in range(0, len(summary), 4000)]
                    
                    # Отправляем части одну за другой
                    for i, part in enumerate(parts):
                        # Добавляем пометку о части для длинных дайджестов
                        if len(parts) > 1:
                            part_header = f"<b>Часть {i+1}/{len(parts)}</b>\n\n"
                            if i > 0:  # Для всех частей кроме первой
                                part = part_header + part
                        
                        # Отправляем сообщение
                        await self.bot.send_message(
                            chat_id=chat_id,
                            text=part,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=False
                        )
                else:
                    # Отправляем дайджест одним сообщением
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=summary,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False
                    )
            except Exception as e:
                logger.error(f"Ошибка при генерации итогового дайджеста: {e}")
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Произошла ошибка при создании дайджеста канала @{source}. Попробуйте позже.",
                    parse_mode=ParseMode.HTML
                )
                
        except Exception as e:
            logger.error(f"Ошибка при генерации дайджеста: {e}")
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Произошла ошибка при генерации дайджеста канала @{source}.\n"
                         f"Пожалуйста, попробуйте позже.",
                    parse_mode=ParseMode.HTML
                )
            except Exception as send_error:
                logger.error(f"Не удалось отправить сообщение об ошибке: {send_error}")

    async def web_interface(self, message: Message, state: FSMContext):
        """Обработчик для доступа к веб-интерфейсу"""
        if not self.web_module or not self.web_interface_url:
            await message.answer(
                "❌ Веб-интерфейс не настроен или недоступен.\n"
                "Пожалуйста, проверьте настройки в файле .env:"
                "```\n"
                "FLASK_HOST=0.0.0.0\n"
                "FLASK_PORT=5000\n"
                "WEB_INTERFACE_URL=http://localhost:5000/digest\n"
                "```"
            )
            return
            
        user_id = message.from_user.id
        username = message.from_user.username
        first_name = message.from_user.first_name
        last_name = message.from_user.last_name
        
        # Генерируем уникальный токен доступа для пользователя
        import hashlib
        import time
        
        # Создаем токен на основе ID пользователя, времени и секретного ключа
        secret_key = os.getenv('SECRET_KEY', 'default_secret_key')
        current_time = str(int(time.time()))
        token_data = f"{user_id}:{current_time}:{secret_key}"
        access_token = hashlib.sha256(token_data.encode()).hexdigest()
        
        try:
            # Сохраняем токен в базе данных с помощью менеджера БД
            from db_manager import MongoDBManager
            db_manager = MongoDBManager()
            
            # Установка срока действия токена (24 часа)
            import datetime
            expiry = datetime.datetime.now() + datetime.timedelta(hours=24)
            
            # Сохраняем токен и данные пользователя
            result = db_manager.save_web_token(
                user_id=user_id,
                token=access_token,
                username=username or first_name,
                expiry=expiry
            )
            
            if not result:
                # Если не удалось сохранить токен, генерируем URL без авторизации
                logger.warning(f"Не удалось сохранить токен для пользователя {user_id}")
                personalized_url = self.web_interface_url
            else:
                # Создаем персонализированную URL с токеном
                personalized_url = f"{self.web_interface_url}?token={access_token}&username={username or first_name}"
            
            # Создаем inline-кнопку для открытия веб-интерфейса в обычном режиме
            inline_markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🌐 Открыть в браузере", url=personalized_url)]
            ])
            
            # Создаем WebApp кнопку для открытия приложения внутри Telegram
            webapp_button = InlineKeyboardButton(
                text="📱 Открыть в Telegram",
                web_app=WebAppInfo(url=personalized_url)
            )
            
            # Добавляем WebApp кнопку
            inline_markup.inline_keyboard.insert(0, [webapp_button])
            
            # Отправляем сообщение с кнопками
            await message.answer(
                f"🌐 <b>Веб-интерфейс дайджестов</b>\n\n"
                f"Теперь вы можете использовать удобный веб-интерфейс для работы с дайджестами.\n"
                f"Выберите способ открытия интерфейса:\n\n"
                f"• <b>Открыть в Telegram</b> - запустит приложение внутри Telegram\n"
                f"• <b>Открыть в браузере</b> - откроет интерфейс в вашем браузере",
                reply_markup=inline_markup,
                parse_mode=ParseMode.HTML
            )
                
        except Exception as e:
            logger.error(f"Ошибка при создании ссылки на веб-интерфейс: {e}")
            await message.answer(
                "❌ Произошла ошибка при создании ссылки на веб-интерфейс.\n"
                "Пожалуйста, попробуйте позже."
            )
    
    async def process_webapp_data(self, message: Message):
        """Обработчик для получения данных от веб-приложения"""
        try:
            # Получаем данные из веб-приложения
            data = json.loads(message.web_app_data.data)
            
            # Обрабатываем разные типы действий
            if data.get('action') == 'share_digest':
                digest_text = data.get('digest', 'Дайджест не найден')
                
                # Отправляем дайджест в чат
                await message.answer(
                    f"📰 <b>Ваш экономический дайджест</b>\n\n{digest_text}",
                    parse_mode=ParseMode.HTML
                )
            else:
                await message.answer(
                    "Получены данные из веб-приложения, но действие не распознано."
                )
        except Exception as e:
            logger.error(f"Ошибка при обработке данных из веб-приложения: {e}")
            await message.answer(
                "Произошла ошибка при обработке данных из веб-приложения."
            )

if __name__ == '__main__':
    # Создаем экземпляр бота
    bot = NewsBot()
    # Запускаем бота в асинхронном режиме
    asyncio.run(bot.run())