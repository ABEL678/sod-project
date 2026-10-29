# ARCHITECTURE.md — СОД для проектной компании

**Версия:** 1.1
**Дата:** 2026-10-28
**Статус:** Принято, обязательно к соблюдению при генерации кода

## 1. Контекст

Небольшая проектная компания (25 человек). Своя СОД на Python/Django. Разработка — 1 человек + ИИ-агент Kilo в PyCharm. Бюджет ограничен. Цель — порядок в файлах, версионность, права, а в будущем — почта, замечания, ИИ-поиск.

## 2. Архитектурное решение

Модульный монолит на Django с «гексагон лайт» внутри каждого приложения: бизнес-логика изолирована в domain/services.py, адаптеры (views, почтовые задачи) вызывают её, внешние зависимости скрыты за портами.

### 2.1. Структура проекта

sod_project/
├── ARCHITECTURE.md        # этот файл
├── sod_core/              # настройки Django, URLs, общие утилиты
├── storage/               # ЯДРО
│   ├── domain/
│   │   ├── models.py      # Project, Section, Document, DocumentVersion, UserProfile, ProjectMembership, Company, DocumentView
│   │   └── services.py    # upload_document(), create_project(), check_write_permission(), get_accessible_documents(), generate_vedomost(), search_documents()
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

### 2.2. Ключевые правила

1. Бизнес-логика — только в domain/services.py. Views, почтовые задачи и ИИ-агент вызывают одни и те же функции. upload_document(user, section, file) используется и веб-интерфейсом, и модулем почты.
2. Внешние зависимости — за портами. Хранилище файлов определяется интерфейсом FileStorage в ports/. Локальный диск и S3 — две реализации. Смена хранилища не требует правок в домене.
3. Модули общаются через сервисы, а не через модели напрямую. mail_integration не вызывает Document.objects.create() — он вызывает storage.domain.services.upload_document().
4. Никаких микросервисов на старте. Все модули в одном процессе Django. Выделение в отдельный сервис — только при реальной нагрузке.
5. ИИ-агент получает доступ к данным через MCP-сервер. Модуль ai_assistant предоставляет инструменты (search_documents, get_document_text, list_issues), а не прямой доступ к БД.

### 2.3. Что НЕ делаем на старте

- Не проектируем все порты заранее. Порт создается, когда появляется вторая реализация.
- Не вводим Celery, Redis, pgvector, пока нет реальной нагрузки. Начинаем с SQLite и management-команд по расписанию (cron).
- Не пишем MCP-сервер, пока ядро не стабильно.
- Не усложняем права до уровня отдельных документов — только разделы и проекты.

## 3. Схема данных ядра storage

### 3.1. Принципы

1. Документ — логическая сущность, версия — физический файл. Document описывает «что это за чертёж», DocumentVersion — «какой файл лежит на диске и какие лежали раньше».
2. Текущая версия — флаг, а не отдельная таблица. У Document есть current_version (FK на DocumentVersion). Старые версии остаются в БД и на диске.
3. Права — на уровне раздела, а не документа. Инженер получает доступ к разделу «ВК» целиком. Это проще администрировать.
4. Исходные данные — тип документа, а не отдельная модель. Меньше сущностей, проще права и поиск.
5. Мультитенантность — через company. Каждый Project принадлежит Company, каждый пользователь привязан к Company через UserProfile.company. Все запросы в services.py фильтруют по company пользователя. company берётся из request.user.profile.company, никогда из параметров запроса. Модель Company, поле company в Project и поле company в UserProfile добавлены при подготовке к деплою.

### 3.2. Модели

#### Company

| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| name | CharField(255) | Название компании |
| created_at | DateTimeField | |

#### Project

| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| company | FK → Company, on_delete=CASCADE | Принадлежность к компании (мультитенантность) |
| code | CharField(50), unique | Шифр проекта, «ЖК-2026-01» |
| name | CharField(255) | «ЖК Солнечный» |
| customer | CharField(255), blank | Заказчик |
| gip | FK → User, null=True | ГИП проекта |
| status | CharField(20), choices | active, archived |
| created_at | DateTimeField | |
| created_by | FK → User | |

#### Section

| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| project | FK → Project, related_name=sections | |
| code | CharField(20) | «АР», «КР», «ОВ», «ВК», «ИД» |
| name | CharField(255) | «Архитектурные решения» |
| order | IntegerField | Порядок сортировки в ведомости |
| folder_template | CharField(100), blank | Имя шаблона папок для модуля automation |

**Уникальность:** (project, code).

#### Document

| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| project | FK → Project, related_name=documents | |
| section | FK → Section, related_name=documents | |
| code | CharField(100) | Шифр документа, «АР-01-2026» |
| name | CharField(255) | «План 1 этажа» |
| doc_type | CharField(20), choices | drawing, source_data, report, other |
| current_version | FK → DocumentVersion, null=True, related_name=+ | Текущая актуальная версия |
| status | CharField(20), choices | draft, in_review, approved, archived |
| created_at | DateTimeField | |
| created_by | FK → User | |
| updated_at | DateTimeField | Обновляется при каждой новой версии |

**Уникальность:** (project, section, code).

**Важно:** current_version — null=True, потому что документ создается до загрузки первого файла. Заполняется в services.upload_document().

#### DocumentVersion

| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| document | FK → Document, related_name=versions | |
| version_number | IntegerField | 1, 2, 3… |
| file | FileField или CharField (путь через порт) | Физический файл |
| file_size | BigIntegerField | Размер в байтах |
| file_hash | CharField(64), blank | SHA-256, для контроля целостности и дедупликации |
| comment | TextField, blank | Что изменилось в этой версии |
| uploaded_at | DateTimeField | |
| uploaded_by | FK → User | |

**Уникальность:** (document, version_number).

#### UserProfile

Расширение стандартного User Django (через OneToOneField).

| Поле | Тип | Описание |
|---|---|---|
| user | OneToOne → User | |
| company | FK → Company, null=True, on_delete=SET_NULL | Компания пользователя (мультитенантность) |
| role | CharField(20), choices | admin, gip, engineer, viewer, contractor |
| allowed_sections | ManyToMany → Section, blank | Разделы, куда пользователь может писать |

**Правила ролей:**
- admin — всё.
- gip — всё в своих проектах (через Project.gip и ProjectMembership).
- engineer — пишет только в allowed_sections, читает всё в своих проектах.
- viewer — только чтение.
- contractor — читает только разделы из allowed_sections.

#### ProjectMembership

| Поле | Тип | Описание |
|---|---|---|
| project | FK → Project | |
| user | FK → User | |
| role_in_project | CharField(20) | gip, engineer, viewer, contractor |

**Уникальность:** (project, user).

Позволяет одному инженеру быть ГИПом в одном проекте и рядовым проектировщиком в другом.

#### DocumentView (Этап 7)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| project | FK → Project | |
| user | FK → User, null=True | NULL = общий вид для всех участников проекта |
| name | CharField(100) | «Мой вид: только шифры» |
| config | JSONField | {"columns": [...], "filters": {...}, "sort": [...], "widths": {"code": 120, "name": 300}}; ширины колонок — 50–800 px |
| is_shared | BooleanField | Общий или личный |
| created_at | DateTimeField | |

#### Folder (T8-CORE)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| project | FK → Project, on_delete=CASCADE | Проект |
| section | FK → Section, null=True | Раздел (если папка на уровне раздела) |
| parent | FK → Folder, null=True | Родительская папка (для вложенности) |
| name | CharField(255) | Название папки |
| order | IntegerField | Порядок сортировки |
| created_at | DateTimeField | |
| created_by | FK → User | |

**Правило:** папка может быть на уровне проекта (section=None) или внутри раздела (section=Section). Вложенность через parent.

#### Document (дополнение к T8-CORE)

Добавить поле:
- `public_link_token` (UUIDField, null=True, blank=True, unique=True) — для генерации публичных ссылок на документ/версию.

#### DocumentStatus (Enum для жизненного цикла)

- `WIP` — в работе (черновик, не передан).
- `SHARED` — передан смежникам (для координации).
- `PUBLISHED` — выпущен заказчику (утверждён).
- `ARCHIVED` — архив.

**Правило:** статус меняется через сервис `update_document_status()`, а не напрямую.

#### IssueComment (Этап 9)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| issue | FK → Issue, related_name='comments' | |
| author | FK → User | |
| text | TextField | Текст комментария |
| created_at | DateTimeField | |

#### IssueAttachment (Этап 9)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| issue | FK → Issue, related_name='attachments' | |
| file | FileField | Через порт FileStorage |
| filename | CharField(255) | |
| uploaded_at | DateTimeField | |

**Изменения в Issue (Этап 8 и 9):**
- `page_number` (int, null=True)
- `x_ratio` (float, null=True)
- `y_ratio` (float, null=True)
- `assignee` (FK User, null=True)
- `due_date` (DateField, null=True)
**Constraint:** `(page_number IS NULL AND x_ratio IS NULL AND y_ratio IS NULL) OR (page_number IS NOT NULL AND x_ratio IS NOT NULL AND y_ratio IS NOT NULL)`

### 3.3. Связи

User 1─1 UserProfile
Company 1─N UserProfile
Company 1─N Project
User 1─N ProjectMembership N─1 Project
Project 1─N Section
Project 1─N Document
Section 1─N Document
Document 1─N DocumentVersion
Document N─1 DocumentVersion (current_version)
User 1─N DocumentVersion (uploaded_by)
UserProfile N─N Section (allowed_sections)
User 1─N DocumentView N─1 Project

### 3.4. Что это дает для будущих модулей

- **Ведомости:** запрос Document.objects.filter(project=..., section=...).select_related('current_version') → таблица с гиперссылками на current_version.file.url. Экспорт в Excel — тот же запрос + openpyxl.
- **Замечания:** модель Issue получает FK на Document и DocumentVersion (замечание привязано к конкретной версии).
- **Почта:** вызывает services.upload_document() с doc_type='source_data' или обычным. Правила сортировки определяют project и section по теме/отправителю.
- **ИИ-поиск:** индексирует DocumentVersion (текст из PDF/DOCX), хранит эмбеддинги с FK на DocumentVersion. Поиск возвращает документы, пользователю показываем актуальную версию.

## 4. Контракты сервисов ядра

Все модули вызывают только эти функции. Сигнатуры фиксированы.

def create_project(
    company: Company,
    code: str,
    name: str,
    customer: str,
    gip: User | None = None,
    created_by: User | None = None,
) -> Project: ...

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

def get_user_company(user: User) -> Company | None: ...

def get_accessible_projects(user: User) -> QuerySet[Project]: ...

def get_accessible_documents(user: User) -> QuerySet[Document]: ...

def get_accessible_sections(user: User, project: Project) -> QuerySet[Section]: ...

def generate_vedomost(
    project: Project,
    section: Section | None = None,
    *,
    company: Company,
    user: User | None = None,
) -> list[dict]: ...

**Правило:** `get_accessible_documents(user)` ограничивает выборку компанией пользователя, а для роли `contractor` — ещё и разделами из `UserProfile.allowed_sections`. `get_accessible_sections(user, project)` делает то же для дерева разделов и фильтров поиска. `generate_vedomost(..., user=...)` использует эту же выборку, поэтому ведомость и Excel не показывают contractor'у чужие разделы.

def search_documents(
    user: User,
    project: Project,
    query: str = "",
    section: Section | None = None,
    doc_type: str | None = None,
    status: str | None = None,
) -> list[Document]: ...

**Правило поиска:** `search_documents()` ищет по `Document.code`, `Document.name` и `DocumentVersion.comment` через `icontains`/`Q`, всегда применяет `get_accessible_documents()` и фильтр проекта. Поиск только по метаданным, без полнотекстового индекса.

def save_document_view(
    user: User,
    project: Project,
    name: str,
    config: dict,
    is_shared: bool = False,
) -> DocumentView: ...

def list_document_views(user: User, project: Project) -> list[DocumentView]: ...

### Модуль issues (Этап 8-9)

def create_issue(
    user: User,
    document: Document,
    version: DocumentVersion | None,
    description: str,
    page_number: int | None = None,
    x_ratio: float | None = None,
    y_ratio: float | None = None,
) -> Issue: ...
# Проверяет права на чтение документа. Если версия указана, проверяет, что она принадлежит документу.

def get_issues_for_document(document: Document, version: DocumentVersion | None = None) -> QuerySet[Issue]: ...
# Без version — все замечания документа. С version — только к этой версии.

def add_issue_comment(user: User, issue: Issue, text: str) -> IssueComment: ...
def attach_file_to_issue(user: User, issue: Issue, file) -> IssueAttachment: ...
def update_issue_status(user: User, issue: Issue, status: str) -> Issue: ...
def get_my_issues(user: User, role: str = 'assignee') -> QuerySet[Issue]: ...

### Модуль Folder (T8-CORE)

def create_folder(
    user: User,
    project: Project,
    name: str,
    section: Section | None = None,
    parent: Folder | None = None,
) -> Folder: ...

def move_document(
    user: User,
    document: Document,
    folder: Folder,
) -> Document: ...

def get_folder_tree(user: User, project: Project) -> list[Folder]: ...

def generate_share_link(user: User, document: Document) -> str: ...

### Модуль Document (дополнение)

def update_document_status(
    user: User,
    document: Document,
    new_status: str,  # WIP, SHARED, PUBLISHED, ARCHIVED
) -> Document: ...

**Правило:** upload_document() — единственная точка создания версий. Ни views, ни почтовый модуль, ни ИИ не создают DocumentVersion напрямую.

## 5. Порты

### 5.1. FileStorage (ports/file_storage.py)

class FileStorage(Protocol):
    def save(self, path: str, file) -> str: ...
    def get(self, path: str): ...
    def delete(self, path: str) -> None: ...

Реализации: adapters/local_storage.py (сейчас), adapters/s3_storage.py (позже, без правок домена).

### 5.2. ExternalIntegrationPort (ports/integration.py) — новый порт

Интерфейс для общения с внешними системами (ГИП-Ассистент, ВОР-билдер, Витро-трекер). Реализации — адаптеры в `storage/adapters/`.

---

## 6. Экосистема и интеграция (API Gateway)

СОД (Stream-CAD) выступает центральным хабом (API Gateway) для экосистемы из четырех продуктов:
1. **ГИП-Ассистент** (FastAPI/React) — управление задачами (Goal/Decision/Task/RFI).
2. **ВОР-билдер** (Python/NiceGUI) — генерация ведомостей объёмов работ (ВОР).
3. **Витро-трекер** (Python/Streamlit) — синхронизация замечаний из Витрокад.
4. **СОД (Stream-CAD)** — наш продукт, хранение файлов и версий.

### 6.1. Принципы интеграции (Anti-Corruption Layer)
- СОД — источник истины для файлов и версий.
- Все системы ходят в СОД через **REST API** (Django JSON-ответы + токен-аутентификация).
- Аутентификация — по API-ключам, привязанным к `Company`.
- В СОД создается порт `ExternalIntegrationPort` и Django-приложение `external_api`.

### 6.2. Маппинг сущностей (СОД ↔ Другие системы)
| СОД (Stream-CAD) | ГИП-Ассистент | ВОР-билдер | Витро-трекер |
|---|---|---|---|
| `Project.code` | `Project.code` | `spec_code` (префикс) | Шифр комплекта (`complex`) |
| `Document.code` | `Document.cipher` | `Specification` (СО) | Шифр листа (`cipher`) |
| `DocumentVersion.version_number` | `DocumentVersion.version` | `revision` | `vitro_version` |
| `Issue` | `Task` (или `RFI`) | — | `comments` (замечания) |
| `Issue.description` | `Task.formulation` | — | `note_text` |

### 6.3. Контракты API СОД (для внутренних систем)
- `POST /api/v1/projects/` — создать проект (ГИП-Ассистент).
- `GET /api/v1/projects/{code}/specifications/` — получить список СО (ВОР-билдер).
- `POST /api/v1/projects/{code}/vor/` — загрузить ВОР (ВОР-билдер).
- `POST /api/v1/projects/{code}/issues/sync/` — синхронизировать замечания (Витро-трекер).
- `GET /api/v1/projects/{code}/issues/pending/` — получить обновленные замечания (Витро-трекер).

### 6.4. Интеграция с Витрокад (SharePoint)
- **Маркапы на PDF:** Клик по чертежу → создание замечания с координатами (пин). В Vitro-CAD это стандарт. У нас это **Этап 8**.
- **Панель замечаний с чатом:** Слева — документ, справа — список замечаний с обсуждением (как в Sarex #7). У нас это **Этап 9**.
- **Сравнение версий:** Наложение слоев PDF.js (Этап 8.1).
- **Синхронизация с SharePoint:** Используем `httpx` для REST API SharePoint (аналогично Витро-трекеру).

---

## 7. Правила для ИИ-агента Kilo

При генерации кода обязательно:

1. Следовать структуре из раздела 2.1.
2. Бизнес-логику писать только в domain/services.py, не во views.
3. Использовать upload_document() как единственную точку создания версий.
4. Не добавлять Celery, Redis, pgvector, MCP-сервер, пока явно не попросят.
5. Не менять модели из раздела 3.2 без обновления этого файла.
6. Не предлагать микросервисы и альтернативные фреймворки.
7. Не создавать API-эндпоинты напрямую во views — только через сервисы и порты.