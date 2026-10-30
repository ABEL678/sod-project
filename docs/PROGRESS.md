# PROGRESS.md — Статус проекта

**Дата обновления:** 30.10.2026

## Текущий этап
**T8-CORE: Фундамент СОД (папки, проекты, ссылки, корзина).** 
Этап 7 (UI Refactoring) полностью закрыт (T6 + T7-ui-final). 
Система развернута в production: **https://stream-cad.ru/**

## Что сделано
- [x] **Развертывание:** Django-проект, настройки, локальный Bootstrap.
- [x] **Продакшн:** Система развернута на сервере, доступна по адресу https://stream-cad.ru/ (Nginx, Gunicorn, HTTPS).
- [x] **Модели ядра:** Company, Project, Section, Document, DocumentVersion, UserProfile, ProjectMembership, DocumentView.
- [x] **Мультитенантность:** Project.company и UserProfile.company, фильтрация проектов/документов по компании.
- [x] **Production-настройки:** SECRET_KEY/DEBUG/ALLOWED_HOSTS/CSRF_TRUSTED_ORIGINS из окружения.
- [x] **Сервисы (Бизнес-логика):** upload_document, create_project, check_write_permission, generate_vedomost, save_document_view.
- [x] **Интерфейс (UI):** Explorer с деревом разделов, Drag-and-Drop, таблица документов, вкладка «Участники».
- [x] **История версий:** Страница документа со списком версий.
- [x] **Поиск по ревизиям:** Поиск по шифру/названию/комментарию, фильтры, быстрый переход к версии.
- [x] **Экспорт в Excel:** Генерация .xlsx с гиперссылками на текущие версии.
- [x] **Замечания (Issues):** Создание замечаний, смена статуса.
- [x] **Почта (Симуляция):** Модель MailRule, импорт из почты.
- [x] **Аутентификация:** Вход/выход.
- [x] **GUI (Этап 7):** Сохраняемые виды таблицы (DocumentView), счётчики документов, вкладка «Участники».
- [x] **GUI (T7-ui-final):** Внедрён django-scotty (таблицы, фильтры, пагинация, экспорт). Кастомный шаблон documents_table.html. Новый топбар с компанией.
- [x] **Документация:** ARCHITECTURE.md, MASTER_SPEC.md, CONTEXT.md, PROGRESS.md обновлены.

## Журнал (Changelog)

### 30.10.2026 — T11-ROLES
- ГОСТ-роли в `UserProfile.role`: admin, gip, developer, checker, normocontrol, customer, contractor, viewer.
- Data-миграция `engineer` → `developer` (`UserProfile` и `ProjectMembership`).
- Права на файлы: developer и contractor пишут в allowed_sections (WIP); checker, normocontrol, customer, viewer — только чтение.
- Статусы `Issue`: NEW, IN_PROGRESS, REJECTED, DISCUSSION, DONE, CLOSED, CANCELLED; поля `closed_at`, `closed_by`.
- Матрица переходов в `update_issue_status()`, `check_issue_permission()`.
- UI карточки замечания: цветной бейдж и кнопки статусов только по матрице; ГОСТ-названия ролей.
- Data-миграция статусов Issue: open/in_progress/closed → NEW/IN_PROGRESS/CLOSED.
- Добавлены тесты матрицы статусов и ролей; все тесты зелёные.

### 30.10.2026 — T10-LIFECYCLE
- Добавлена модель `Folder.zone` (WIP, SHARED, PUBLISHED, ARCHIVE).
- Автосоздание 4 зон при старте проекта.
- Связь статуса документа и зоны (move_document).
- Права по зонам (check_zone_permission).
- Иконки зон в дереве (карандаш, люди, щит, замок).
- 233 теста зелёные.

### 29.10.2026 — T7-ui-final (django-scotty)
- Установлен django-scotty и зависимости (django-cotton, django-tables2, django-filters, htmx).
- Созданы `storage/adapters/tables.py` (DocumentTable) и `filters.py` (DocumentFilter).
- `ProjectDetailView` переведён на `CottonTableView` с пагинацией (50 строк).
- Ручная HTML-таблица заменена на `{% render_table table %}`.
- Создан кастомный шаблон `documents_table.html` (sticky, resizable).
- Экспорт переведён на scotty (`{% export_url 'xlsx' %}`).
- 127 тестов зелёные (добавлены тесты на фильтры, пагинацию, экспорт).

### 29.10.2026 — T7-polish / T8-prep blockers
- Создан общий топбар (`_topbar.html`) для всех страниц.
- Исправлен fallback компании (через `project.company.name`).
- Добавлена валидация `x_ratio`/`y_ratio` без `page_number`.
- Viewer/contractor больше не могут создавать замечания (check_write_permission).
- 124 теста зелёные.

### 29.10.2026 — Документация и планирование
- Обновлён ARCHITECTURE.md: добавлены разделы «Порты (ExternalIntegrationPort)» и «Экосистема (API Gateway)».
- Обновлён MASTER_SPEC.md: добавлены Этапы 8-11 (PDF, Карточка замечания, Экспорт PDF, API Gateway).
- Обновлён PROGRESS.md: синхронизирован с MASTER_SPEC.md.

### 28.10.2026 — T6 (Этап 7)
- Добавлена модель DocumentView (личные и общие виды) и миграция 0006.
- В services.py добавлены save_document_view() / list_document_views(), get_sections_with_counts(), смена ролей.
- Страница проекта: настройка столбцов/фильтров/сортировки, счётчики, вкладка «Участники».
- Вкладка «Участники» и общие виды доступны только ГИПу/Админу.

### 28.10.2026 — T6-fix (по итогам аудита)
- Добавлен CheckConstraint documentview_shared_owner_consistency (миграция 0007).
- Запрещено снятие роли последнего ГИПа.
- Contractor не может управлять участниками и создавать общие виды.
- Запросы участников вынесены в сервисы.
- Добавлены тесты (35 тестов T6, всего 110).

### 28.10.2026 — T4 (Этап 6)
- Реализован search_documents() с фильтрами и правами.
- Добавлен get_accessible_sections() для contractor.
- Страница поиска с фильтрами, «Последние документы».

### 28.10.2026 — T3
- get_accessible_documents() ограничивает contractor разделами из allowed_sections.
- Ведомость и Excel-экспорт используют эту выборку.

### 28.10.2026 — T1/T2
- Добавлена модель Company; Project.company и UserProfile.company.
- Миграция 0005 создает компанию и привязывает существующие проекты.
- settings.py читает переменные из env; PostgreSQL выбирается при заданном POSTGRES_DB.

### 28.10.2026
- Созданы все основные модели и миграции.
- Написан services.py, реализован Drag-and-Drop, история версий, экспорт в Excel, замечания.
- Настроена авторизация.

## Блокеры
Нет.

## Бэклог (Что делать дальше)
- [x] **Задача T1:** Подготовка production-настроек.
- [x] **Задача T2:** Добавление модели Company.
- [x] **Этап 6:** Поиск и навигация по ревизиям.
- [x] **Этап 7:** GUI-улучшения (виды таблицы, участники).
- [x] **T7-ui-final:** Внедрение django-scotty.
- [x] **Деплой:** Настройка сервера (Nginx, Gunicorn, домен, HTTPS) — https://stream-cad.ru/.
- [ ] **T8-CORE:** Фундамент СОД (папки, проекты, ссылки, корзина) — В РАБОТЕ.
- [ ] **T9: UI Consistency:** Приведение всех страниц к единому стилю (топбар, таблицы scotty).
- [ ] **T10:** PDF-вьювер и визуальные замечания (пины).
- [ ] **T11:** Карточка замечания 2.0 (обсуждение, сроки, реестр, Excel).
- [ ] **T12:** Экспорт PDF с наложенными замечаниями (PyMuPDF).
- [ ] **T13:** Интеграция с экосистемой (API Gateway).