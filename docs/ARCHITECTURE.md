# ARCHITECTURE.md — СОД для проектной компании

**Версия:** 1.0
**Дата:** 2026-10-27
**Статус:** Принято, обязательно к соблюдению при генерации кода

## 1. Контекст

Небольшая проектная компания (25 человек). Своя СОД на Python/Django. Разработка — 1 человек + ИИ-агент Kilo в PyCharm. Бюджет ограничен. Цель — порядок в файлах, версионность, права, а в будущем — почта, замечания, ИИ-поиск.

## 2. Архитектурное решение

**Модульный монолит на Django** с **«гексагон лайт»** внутри каждого приложения: бизнес-логика изолирована в `domain/services.py`, адаптеры (views, почтовые задачи) вызывают её, внешние зависимости скрыты за портами.

### 2.1. Структура проекта

```
sod_project/
├── ARCHITECTURE.md        # этот файл
├── sod_core/              # настройки Django, URLs, общие утилиты
├── storage/               # ЯДРО
│   ├── domain/
│   │   ├── models.py      # Project, Section, Document, DocumentVersion, UserProfile, ProjectMembership
│   │   └── services.py    # upload_document(), create_project(), check_write_permission(), generate_vedomost()
│   ├── ports/
│   │   └── file_storage.py # интерфейс FileStorage: save(), get(), delete()
│   ├── adapters/
│   │   ├── local_storage.py # реализация порта для локального диска
│   │   ├── views.py
│   │   └── templates/
│   └── tests/
├── automation/            # автосоздание структуры папок по шаблону
├── issues/                # замечания и исходные данные
├── mail_integration/      # IMAP, правила сортировки, выгрузка вложений
├── ai_assistant/          # MCP-сервер, семантический поиск
└── media/                 # файловое хранилище
```

### 2.2. Ключевые правила

1. **Бизнес-логика — только в `domain/services.py`.** Views, почтовые задачи и ИИ-агент вызывают одни и те же функции. `upload_document(user, section, file)` используется и веб-интерфейсом, и модулем почты.
2. **Внешние зависимости — за портами.** Хранилище файлов определяется интерфейсом `FileStorage` в `ports/`. Локальный диск и S3 — две реализации. Смена хранилища не требует правок в домене.
3. **Модули общаются через сервисы, а не через модели напрямую.** `mail_integration` не вызывает `Document.objects.create()` — он вызывает `storage.domain.services.upload_document()`.
4. **Никаких микросервисов на старте.** Все модули в одном процессе Django. Выделение в отдельный сервис — только при реальной нагрузке.
5. **ИИ-агент получает доступ к данным через MCP-сервер.** Модуль `ai_assistant` предоставляет инструменты (`search_documents`, `get_document_text`, `list_issues`), а не прямой доступ к БД.

### 2.3. Что НЕ делаем на старте

- Не проектируем все порты заранее. Порт создается, когда появляется вторая реализация.
- Не вводим Celery, Redis, pgvector, пока нет реальной нагрузки. Начинаем с SQLite и management-команд по расписанию (cron).
- Не пишем MCP-сервер, пока ядро не стабильно.
- Не усложняем права до уровня отдельных документов — только разделы и проекты.

## 3. Схема данных ядра `storage`

### 3.1. Принципы

1. **Документ — логическая сущность, версия — физический файл.** `Document` описывает «что это за чертёж», `DocumentVersion` — «какой файл лежит на диске и какие лежали раньше».
2. **Текущая версия — флаг, а не отдельная таблица.** У `Document` есть `current_version` (FK на `DocumentVersion`). Старые версии остаются в БД и на диске.
3. **Права — на уровне раздела, а не документа.** Инженер получает доступ к разделу «ВК» целиком. Это проще администрировать.
4. **Исходные данные — тип документа, а не отдельная модель.** Меньше сущностей, проще права и поиск.

### 3.2. Модели

#### `Project`

| Поле | Тип | Описание |
|---|---|---|
| `id` | PK | |
| `code` | CharField(50), unique | Шифр проекта, «ЖК-2026-01» |
| `name` | CharField(255) | «ЖК Солнечный» |
| `customer` | CharField(255), blank | Заказчик |
| `gip` | FK → User, null=True | ГИП проекта |
| `status` | CharField(20), choices | `active`, `archived` |
| `created_at` | DateTimeField | |
| `created_by` | FK → User | |

#### `Section`

| Поле | Тип | Описание |
|---|---|---|
| `id` | PK | |
| `project` | FK → Project, related_name=`sections` | |
| `code` | CharField(20) | «АР», «КР», «ОВ», «ВК», «ИД» |
| `name` | CharField(255) | «Архитектурные решения» |
| `order` | IntegerField | Порядок сортировки в ведомости |
| `folder_template` | CharField(100), blank | Имя шаблона папок для модуля automation |

**Уникальность:** `(project, code)`.

#### `Document`

| Поле | Тип | Описание |
|---|---|---|
| `id` | PK | |
| `project` | FK → Project, related_name=`documents` | |
| `section` | FK → Section, related_name=`documents` | |
| `code` | CharField(100) | Шифр документа, «АР-01-2026» |
| `name` | CharField(255) | «План 1 этажа» |
| `doc_type` | CharField(20), choices | `drawing`, `source_data`, `report`, `other` |
| `current_version` | FK → DocumentVersion, null=True, related_name=`+` | Текущая актуальная версия |
| `status` | CharField(20), choices | `draft`, `in_review`, `approved`, `archived` |
| `created_at` | DateTimeField | |
| `created_by` | FK → User | |
| `updated_at` | DateTimeField | Обновляется при каждой новой версии |

**Уникальность:** `(project, section, code)`.

**Важно:** `current_version` — `null=True`, потому что документ создается до загрузки первого файла. Заполняется в `services.upload_document()`.

#### `DocumentVersion`

| Поле | Тип | Описание |
|---|---|---|
| `id` | PK | |
| `document` | FK → Document, related_name=`versions` | |
| `version_number` | IntegerField | 1, 2, 3… |
| `file` | FileField или CharField (путь через порт) | Физический файл |
| `file_size` | BigIntegerField | Размер в байтах |
| `file_hash` | CharField(64), blank | SHA-256, для контроля целостности и дедупликации |
| `comment` | TextField, blank | Что изменилось в этой версии |
| `uploaded_at` | DateTimeField | |
| `uploaded_by` | FK → User | |

**Уникальность:** `(document, version_number)`.

#### `UserProfile`

Расширение стандартного `User` Django (через `OneToOneField`).

| Поле | Тип | Описание |
|---|---|---|
| `user` | OneToOne → User | |
| `role` | CharField(20), choices | `admin`, `gip`, `engineer`, `viewer`, `contractor` |
| `allowed_sections` | ManyToMany → Section, blank | Разделы, куда пользователь может писать |

**Правила ролей:**
- `admin` — всё.
- `gip` — всё в своих проектах (через `Project.gip` и `ProjectMembership`).
- `engineer` — пишет только в `allowed_sections`, читает всё в своих проектах.
- `viewer` — только чтение.
- `contractor` — читает только разделы из `allowed_sections`.

#### `ProjectMembership`

| Поле | Тип | Описание |
|---|---|---|
| `project` | FK → Project | |
| `user` | FK → User | |
| `role_in_project` | CharField(20) | `gip`, `engineer`, `viewer`, `contractor` |

**Уникальность:** `(project, user)`.

Позволяет одному инженеру быть ГИПом в одном проекте и рядовым проектировщиком в другом.

### 3.3. Связи
User 1─1 UserProfile
User 1─N ProjectMembership N─1 Project
Project 1─N Section
Project 1─N Document
Section 1─N Document
Document 1─N DocumentVersion
Document N─1 DocumentVersion (current_version)
User 1─N DocumentVersion (uploaded_by)
UserProfile N─N Section (allowed_sections)


### 3.4. Что это дает для будущих модулей

- **Ведомости:** запрос `Document.objects.filter(project=..., section=...).select_related('current_version')` → таблица с гиперссылками на `current_version.file.url`. Экспорт в Excel — тот же запрос + `openpyxl`.
- **Замечания:** модель `Issue` получает FK на `Document` и `DocumentVersion` (замечание привязано к конкретной версии).
- **Почта:** вызывает `services.upload_document()` с `doc_type='source_data'` или обычным. Правила сортировки определяют `project` и `section` по теме/отправителю.
- **ИИ-поиск:** индексирует `DocumentVersion` (текст из PDF/DOCX), хранит эмбеддинги с FK на `DocumentVersion`. Поиск возвращает документы, пользователю показываем актуальную версию.

## 4. Контракты сервисов ядра

Все модули вызывают только эти функции. Сигнатуры фиксированы.

```python
# storage/domain/services.py

def create_project(code: str, name: str, customer: str, gip: User) -> Project: ...

def create_section(project: Project, code: str, name: str, order: int) -> Section: ...

def upload_document(
    user: User,
    project: Project,
    section: Section,
    code: str,
    name: str,
    doc_type: str,
    file,
    comment: str = "",
) -> DocumentVersion: ...

def check_write_permission(user: User, section: Section) -> bool: ...

def check_read_permission(user: User, document: Document) -> bool: ...

def generate_vedomost(project: Project, section: Section | None = None) -> list[dict]: ...
```
Правило: upload_document() — единственная точка создания версий. Ни views, ни почтовый модуль, ни ИИ не создают DocumentVersion напрямую.

## 5. Порты
### 5.1. FileStorage (ports/file_storage.py)
python
class FileStorage(Protocol):
    def save(self, path: str, file) -> str: ...
    def get(self, path: str): ...
    def delete(self, path: str) -> None: ...
Реализации: adapters/local_storage.py (сейчас), adapters/s3_storage.py (позже, без правок домена).

## 6. Правила для ИИ-агента Kilo
При генерации кода обязательно:

Следовать структуре из раздела 2.1.

Бизнес-логику писать только в domain/services.py, не во views.

Использовать upload_document() как единственную точку создания версий.

Не добавлять Celery, Redis, pgvector, MCP-сервер, пока явно не попросят.

Не менять модели из раздела 3.2 без обновления этого файла.

Не предлагать микросервисы и альтернативные фреймворки.