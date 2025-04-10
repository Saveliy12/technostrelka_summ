// Основной скрипт для управления интерфейсом генератора дайджестов

document.addEventListener('DOMContentLoaded', () => {
    // Получаем объект Telegram WebApp
    const tgApp = window.Telegram?.WebApp;
    
    // Элементы интерфейса
    const generateBtn = document.getElementById('generate-btn');
    const digestContainer = document.getElementById('digest-container');
    const analysisBlock = document.getElementById('analysis-block');
    const analysisContainer = document.getElementById('analysis-container');
    const styleSelect = document.getElementById('digest-style');
    const newsCountInput = document.getElementById('news-count');
    const includeAnalysisCheck = document.getElementById('include-analysis');
    const sourceUsername = document.getElementById('source-username');
    const addSourceBtn = document.getElementById('add-source-btn');
    const sourcesList = document.getElementById('sources-list');
    const copyBtn = document.getElementById('copy-btn');
    const downloadBtn = document.getElementById('download-btn');
    const shareBtn = document.getElementById('share-btn');

    // Модальное окно для уведомлений
    const modal = new bootstrap.Modal(document.getElementById('errorModal'));
    const modalTitle = document.querySelector('#errorModal .modal-title');
    const modalBody = document.querySelector('#errorModal .modal-body');

    // Инициализация Telegram WebApp
    if (tgApp) {
        console.log('Telegram WebApp инициализирован');
        
        // Изменяем внешний вид под тему Telegram
        applyTelegramTheme();
        
        // Сообщаем Telegram, что приложение готово
        tgApp.ready();
        
        // Показываем основной интерфейс
        tgApp.expand();
    }

    // Инициализация приложения
    initApp();

    // Функция инициализации приложения
    function initApp() {
        // Инициализация элементов интерфейса
        initUIElements();
        
        // Настройка событий
        setupEvents();
        
        // Обработчик ошибок для видео
        setupVideoErrorHandling();
    }

    // Применение темы Telegram
    function applyTelegramTheme() {
        if (!tgApp) return;
        
        // Получаем данные о теме из Telegram
        const colorScheme = tgApp.colorScheme || 'light';
        document.body.classList.toggle('dark-theme', colorScheme === 'dark');
        
        // Применяем цвета из Telegram
        document.documentElement.style.setProperty('--tg-theme-bg-color', tgApp.themeParams.bg_color || '#ffffff');
        document.documentElement.style.setProperty('--tg-theme-text-color', tgApp.themeParams.text_color || '#222222');
        document.documentElement.style.setProperty('--tg-theme-hint-color', tgApp.themeParams.hint_color || '#999999');
        document.documentElement.style.setProperty('--tg-theme-link-color', tgApp.themeParams.link_color || '#2678b6');
        document.documentElement.style.setProperty('--tg-theme-button-color', tgApp.themeParams.button_color || '#3390ec');
        document.documentElement.style.setProperty('--tg-theme-button-text-color', tgApp.themeParams.button_text_color || '#ffffff');
        document.documentElement.style.setProperty('--tg-theme-secondary-bg-color', tgApp.themeParams.secondary_bg_color || '#f0f0f0');
    }

    // Инициализация элементов интерфейса
    function initUIElements() {
        // Загрузка стилей дайджеста
        loadDigestStyles();
        
        // Загрузка источников
        loadSources();
        
        // Привязка обработчиков событий
        generateBtn.addEventListener('click', generateDigest);
        addSourceBtn.addEventListener('click', addSource);
        copyBtn.addEventListener('click', copyDigestToClipboard);
        downloadBtn.addEventListener('click', downloadDigestAsFile);
        if (shareBtn) {
            shareBtn.addEventListener('click', shareDigest);
        }
        includeAnalysisCheck.addEventListener('change', toggleAnalysisVisibility);
        
        // Если это Telegram WebApp, предзаполняем имя пользователя
        if (tgApp && tgApp.initDataUnsafe && tgApp.initDataUnsafe.user) {
            const username = document.getElementById('username');
            if (username) {
                username.value = tgApp.initDataUnsafe.user.username || '';
            }
        }
    }

    // Настройка событий
    function setupEvents() {
        // Привязка обработчиков событий
        generateBtn.addEventListener('click', generateDigest);
        addSourceBtn.addEventListener('click', addSource);
        copyBtn.addEventListener('click', copyDigestToClipboard);
        downloadBtn.addEventListener('click', downloadDigestAsFile);
        if (shareBtn) {
            shareBtn.addEventListener('click', shareDigest);
        }
        includeAnalysisCheck.addEventListener('change', toggleAnalysisVisibility);
        
        // Обработка событий Telegram WebApp
        if (tgApp) {
            // Обработка изменения темы
            tgApp.onEvent('themeChanged', applyTelegramTheme);
            
            // Скрываем кнопку скачивания в мобильном приложении
            if (tgApp.platform !== 'web') {
                if (downloadBtn) downloadBtn.style.display = 'none';
            }
        }
    }

    // Обработка ошибок загрузки видео
    function setupVideoErrorHandling() {
        const loadingVideo = document.getElementById('loadingVideo');
        if (loadingVideo) {
            loadingVideo.addEventListener('error', function() {
                this.classList.add('video-error');
                console.log('Ошибка загрузки видео, показываем резервную анимацию');
            });
            
            // Проверяем, доступно ли видео
            if (loadingVideo.readyState === 0) {
                const videoSrc = loadingVideo.querySelector('source');
                if (!videoSrc || !videoSrc.src) {
                    loadingVideo.classList.add('video-error');
                }
            }
        }
    }

    // Создаем функцию для получения базового URL API
    function getApiBaseUrl() {
        // Если это Telegram WebApp, используем полный URL из переменных окружения или конфигурации
        if (window.Telegram?.WebApp) {
            return 'https://umyhhw-178-176-231-54.ru.tuna.am';
        }
        // Иначе используем относительный путь для запросов на том же домене
        return '';
    }

    // Функция для формирования полного URL API
    function getApiUrl(endpoint) {
        return `${getApiBaseUrl()}${endpoint}`;
    }

    // Загрузка стилей дайджеста
    function loadDigestStyles() {
        fetch(getApiUrl('/api/styles'))
            .then(response => response.json())
            .then(styles => {
                styleSelect.innerHTML = '';
                styles.forEach(style => {
                    const option = document.createElement('option');
                    option.value = style.id;
                    option.textContent = `${style.name} - ${style.description}`;
                    styleSelect.appendChild(option);
                });
            })
            .catch(error => {
                console.error('Ошибка при загрузке стилей:', error);
                showNotification('Ошибка', 'Не удалось загрузить стили дайджеста. Пожалуйста, попробуйте обновить страницу.');
            });
    }

    // Загрузка источников с учетом данных Telegram
    function loadSources() {
        // Настройка заголовков для API запросов
        const headers = {};
        
        // Если есть данные инициализации от Telegram, добавляем их
        if (tgApp && tgApp.initData) {
            headers['Telegram-Data'] = tgApp.initData;
        }
        
        fetch(getApiUrl('/api/sources'), { headers })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(sources => {
                sourcesList.innerHTML = '';
                
                if (!sources || sources.length === 0) {
                    sourcesList.innerHTML = '<li class="list-group-item text-center text-muted">Нет добавленных источников</li>';
                    return;
                }
                
                // Проверяем формат данных - может быть как массив объектов, так и массив строк
                sources.forEach(source => {
                    const li = document.createElement('li');
                    li.className = 'list-group-item source-item';
                    
                    // Обрабатываем разные форматы данных
                    let username, displayName;
                    
                    if (typeof source === 'string') {
                        // Если source - это строка (имя пользователя)
                        username = source;
                        displayName = source;
                    } else if (typeof source === 'object') {
                        // Если source - это объект
                        username = source.username || '';
                        displayName = source.name || source.username || '';
                    }
                    
                    li.innerHTML = `
                        <span class="source-name" data-username="${username}">${displayName} <small class="text-muted">@${username}</small></span>
                        <i class="bi bi-x-circle remove-source" data-username="${username}"></i>
                    `;
                    sourcesList.appendChild(li);
                    
                    // Добавление обработчика для удаления источника
                    li.querySelector('.remove-source').addEventListener('click', function() {
                        removeSource(this.dataset.username);
                    });

                    // Добавление обработчика для просмотра деталей источника
                    li.querySelector('.source-name').addEventListener('click', function() {
                        getSourceDetails(this.dataset.username);
                    });
                });
            })
            .catch(error => {
                console.error('Ошибка при загрузке источников:', error);
                sourcesList.innerHTML = '<li class="list-group-item text-center text-danger">Ошибка загрузки источников</li>';
            });
    }

    // Генерация дайджеста с учетом Telegram WebApp
    function generateDigest() {
        // Проверка наличия источников
        if (sourcesList.querySelector('.text-muted') && sourcesList.querySelector('.text-muted').textContent === 'Нет добавленных источников') {
            showNotification('Внимание', 'Для генерации дайджеста необходимо добавить хотя бы один источник новостей.');
            return;
        }
        
        // Отображение индикатора загрузки
        generateBtn.querySelector('.btn-text').textContent = 'Генерация...';
        generateBtn.disabled = true;
        generateBtn.querySelector('.spinner-border').classList.remove('d-none');
        
        // Показываем анимацию загрузки и скрываем другие элементы
        document.getElementById('initialInfo').classList.add('d-none');
        document.getElementById('loadingIndicator').classList.remove('d-none');
        digestContainer.classList.add('d-none');
        analysisBlock.style.display = 'none';
        
        // Если это Telegram WebApp, показываем индикатор загрузки
        if (tgApp) {
            tgApp.MainButton.setParams({
                text: 'Генерация...',
                is_active: false,
                is_visible: true
            });
            tgApp.MainButton.showProgress();
        }
        
        // Подготовка данных для запроса
        const requestData = {
            style: styleSelect.value,
            news_count: parseInt(newsCountInput.value),
            include_analysis: includeAnalysisCheck.checked
        };
        
        // Добавляем данные из Telegram, если они есть
        if (tgApp && tgApp.initDataUnsafe && tgApp.initDataUnsafe.user) {
            requestData.telegram_user = {
                id: tgApp.initDataUnsafe.user.id,
                username: tgApp.initDataUnsafe.user.username,
                first_name: tgApp.initDataUnsafe.user.first_name,
                last_name: tgApp.initDataUnsafe.user.last_name
            };
        }
        
        // Отправка запроса на генерацию дайджеста
        fetch(getApiUrl('/api/generate-digest'), {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(tgApp && tgApp.initData ? { 'Telegram-Data': tgApp.initData } : {})
            },
            body: JSON.stringify(requestData)
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                throw new Error(data.error);
            }
            
            // Скрываем анимацию загрузки
            document.getElementById('loadingIndicator').classList.add('d-none');
            
            // Отображение дайджеста
            digestContainer.innerHTML = data.digest;
            digestContainer.classList.remove('d-none');
            
            // Показываем кнопки действий
            document.querySelector('.result-actions').classList.remove('d-none');
            
            // Добавление класса для стилизации в зависимости от выбранного стиля
            digestContainer.className = `digest-container digest-${data.style}`;
            
            // Отображение анализа, если он включен
            if (data.analysis && includeAnalysisCheck.checked) {
                analysisContainer.innerHTML = data.analysis;
                analysisBlock.style.display = 'block';
            } else {
                analysisBlock.style.display = 'none';
            }
            
            // Применение анимаций к элементам дайджеста
            applyAnimationsToDigest();
            
            // Если это Telegram WebApp, настраиваем главную кнопку
            if (tgApp) {
                tgApp.MainButton.hideProgress();
                tgApp.MainButton.setParams({
                    text: 'Поделиться дайджестом',
                    is_active: true,
                    color: tgApp.themeParams.button_color
                });
                tgApp.MainButton.onClick(shareDigest);
            }
        })
        .catch(error => {
            console.error('Ошибка при генерации дайджеста:', error);
            
            // Скрываем анимацию загрузки
            document.getElementById('loadingIndicator').classList.add('d-none');
            
            // Показываем ошибку в контейнере дайджеста
            digestContainer.innerHTML = `
                <div class="alert alert-danger">
                    <i class="bi bi-exclamation-triangle-fill me-2"></i>
                    <strong>Ошибка при генерации дайджеста:</strong> ${error.message}
                </div>
                <p class="text-center text-muted">Пожалуйста, проверьте настройки и попробуйте снова.</p>
            `;
            digestContainer.classList.remove('d-none');
            
            // Если это Telegram WebApp, скрываем индикатор загрузки
            if (tgApp) {
                tgApp.MainButton.hideProgress();
                tgApp.MainButton.hide();
            }
        })
        .finally(() => {
            // Восстановление кнопки
            generateBtn.querySelector('.btn-text').textContent = 'Обновить дайджест';
            generateBtn.disabled = false;
            generateBtn.querySelector('.spinner-border').classList.add('d-none');
        });
    }

    // Добавление источника
    function addSource() {
        const username = sourceUsername.value.trim();
        
        if (!username) {
            showNotification('Ошибка', 'Пожалуйста, укажите имя пользователя или ссылку на канал.');
            return;
        }
        
        // Преобразование ссылки или @username в чистое имя пользователя
        let cleanUsername = username;
        if (cleanUsername.startsWith('@')) {
            cleanUsername = cleanUsername.substring(1);
        } else if (cleanUsername.includes('t.me/')) {
            cleanUsername = cleanUsername.split('t.me/')[1];
            if (cleanUsername.includes('/')) {
                cleanUsername = cleanUsername.split('/')[0];
            }
        }
        
        // Отправка запроса на добавление источника
        fetch(getApiUrl('/api/sources'), {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                username: cleanUsername,
                name: cleanUsername
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                throw new Error(data.error);
            }
            
            showNotification('Успех', `Источник @${cleanUsername} успешно добавлен.`);
            sourceUsername.value = '';
            loadSources();
        })
        .catch(error => {
            console.error('Ошибка при добавлении источника:', error);
            showNotification('Ошибка', `Не удалось добавить источник: ${error.message}`);
        });
    }

    // Удаление источника
    function removeSource(username) {
        if (!confirm(`Вы уверены, что хотите удалить источник @${username}?`)) {
            return;
        }
        
        fetch(getApiUrl(`/api/sources/${username}`), {
            method: 'DELETE'
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                throw new Error(data.error);
            }
            
            showNotification('Успех', `Источник @${username} успешно удален.`);
            loadSources();
        })
        .catch(error => {
            console.error('Ошибка при удалении источника:', error);
            showNotification('Ошибка', `Не удалось удалить источник: ${error.message}`);
        });
    }

    // Копирование дайджеста в буфер обмена
    function copyDigestToClipboard() {
        // Проверка наличия дайджеста
        if (digestContainer.querySelector('.text-muted')) {
            showNotification('Внимание', 'Сначала необходимо создать дайджест.');
            return;
        }
        
        // Получение текста дайджеста (только текст, без HTML)
        const digestText = digestContainer.innerText;
        
        // Копирование в буфер обмена
        navigator.clipboard.writeText(digestText)
            .then(() => {
                showNotification('Успех', 'Дайджест скопирован в буфер обмена.');
            })
            .catch(error => {
                console.error('Ошибка при копировании:', error);
                showNotification('Ошибка', 'Не удалось скопировать дайджест. Попробуйте еще раз.');
            });
    }

    // Скачивание дайджеста как файл
    function downloadDigestAsFile() {
        // Проверка наличия дайджеста
        if (digestContainer.querySelector('.text-muted')) {
            showNotification('Внимание', 'Сначала необходимо создать дайджест.');
            return;
        }
        
        // Получение HTML дайджеста
        const digestHTML = digestContainer.innerHTML;
        
        // Создание временной ссылки для скачивания
        const blob = new Blob([digestHTML], { type: 'text/html' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        
        // Настройка ссылки
        const date = new Date();
        const filename = `digest_${date.getFullYear()}-${date.getMonth()+1}-${date.getDate()}.html`;
        
        a.href = url;
        a.download = filename;
        a.style.display = 'none';
        
        // Добавление ссылки на страницу, клик и удаление
        document.body.appendChild(a);
        a.click();
        
        // Очистка
        setTimeout(() => {
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }, 100);
        
        showNotification('Успех', `Дайджест сохранен как ${filename}`);
    }

    // Переключение видимости блока анализа
    function toggleAnalysisVisibility() {
        if (includeAnalysisCheck.checked) {
            analysisBlock.style.display = 'block';
        } else {
            analysisBlock.style.display = 'none';
        }
    }

    // Применение анимаций к элементам дайджеста
    function applyAnimationsToDigest() {
        // Находим заголовки и параграфы в дайджесте
        const headings = digestContainer.querySelectorAll('h1, h2, h3, h4, h5, h6');
        const paragraphs = digestContainer.querySelectorAll('p');
        const lists = digestContainer.querySelectorAll('ul, ol');
        
        // Применяем анимацию с задержкой для плавного появления
        headings.forEach((heading, index) => {
            heading.style.opacity = '0';
            heading.style.transform = 'translateY(15px)';
            heading.style.animation = `fadeIn 0.5s ease ${0.1 + index * 0.05}s forwards`;
        });
        
        paragraphs.forEach((paragraph, index) => {
            paragraph.style.opacity = '0';
            paragraph.style.transform = 'translateY(15px)';
            paragraph.style.animation = `fadeIn 0.5s ease ${0.2 + index * 0.05}s forwards`;
        });
        
        lists.forEach((list, index) => {
            list.style.opacity = '0';
            list.style.transform = 'translateY(15px)';
            list.style.animation = `fadeIn 0.5s ease ${0.3 + index * 0.05}s forwards`;
            
            // Анимация для элементов списка
            const items = list.querySelectorAll('li');
            items.forEach((item, itemIndex) => {
                item.style.opacity = '0';
                item.style.transform = 'translateY(10px)';
                item.style.animation = `fadeIn 0.5s ease ${0.35 + index * 0.05 + itemIndex * 0.03}s forwards`;
            });
        });
    }

    // Получение детальной информации об источнике по username
    function getSourceDetails(username) {
        fetch(getApiUrl(`/api/sources/${username}`))
            .then(response => {
                if (!response.ok) {
                    throw new Error('Источник не найден');
                }
                return response.json();
            })
            .then(source => {
                // Создаем текст с информацией об источнике
                const sourceInfo = `Имя: ${source.name}
Username: @${source.username}
URL: ${source.url}`;
                
                // Показываем информацию в модальном окне
                modalTitle.innerHTML = '<i class="bi bi-info-circle me-2"></i>Информация об источнике';
                document.getElementById('errorMessage').textContent = sourceInfo;
                modal.show();
            })
            .catch(error => {
                console.error('Ошибка при получении информации об источнике:', error);
                showNotification('Ошибка', `Не удалось получить информацию об источнике: ${error.message}`);
            });
    }

    // Отображение уведомления с учетом Telegram
    function showNotification(title, message) {
        // Если это Telegram WebApp, используем нативный Alert
        if (tgApp) {
            tgApp.showAlert(message);
            return;
        }
        
        // Иначе используем модальное окно Bootstrap
        // Меняем иконку и текст заголовка в зависимости от типа уведомления
        if (title.toLowerCase() === 'ошибка') {
            modalTitle.innerHTML = '<i class="bi bi-exclamation-triangle-fill text-danger me-2"></i>Ошибка';
        } else if (title.toLowerCase() === 'успех') {
            modalTitle.innerHTML = '<i class="bi bi-check-circle-fill text-success me-2"></i>Успех';
        } else {
            modalTitle.innerHTML = `<i class="bi bi-info-circle me-2"></i>${title}`;
        }
        
        // Устанавливаем текст сообщения
        document.getElementById('errorMessage').textContent = message;
        
        // Показываем модальное окно
        modal.show();
    }

    // Функция для поделиться дайджестом через Telegram
    function shareDigest() {
        if (!tgApp) {
            showNotification('Внимание', 'Эта функция доступна только в Telegram.');
            return;
        }
        
        // Проверка наличия дайджеста
        if (digestContainer.querySelector('.text-muted') || digestContainer.classList.contains('d-none')) {
            showNotification('Внимание', 'Сначала необходимо создать дайджест.');
            return;
        }
        
        // Получение текста дайджеста
        const digestText = digestContainer.innerText;
        
        // Отправляем данные обратно в бота
        tgApp.sendData(JSON.stringify({
            action: 'share_digest',
            digest: digestText
        }));
        
        // Показываем уведомление
        showNotification('Успех', 'Дайджест отправлен в чат Telegram.');
    }
}); 