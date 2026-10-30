# ARCHITECTURE.md — СОД для проектной компании

**Версия:** 1.4
**Дата:** 2026-10-30
**Статус:** Принято, обязательно к соблюдению при генерации кода

## 1. Контекст

Небольшая проектная компания (25 человек). Своя СОД на Python/Django. Разработка — 1 человек + ИИ-агент Kilo в PyCharm. Бюджет ограничен. Цель — порядок в файлах, версионность, права, а в будущем — почта, замечания, ИИ-поиск. Система развернута в production: https://stream-cad.ru/ (SaaS).

## 2. Архитектурное решение

Модульный монолит на Django с «гексагон лайт» внутри каждого приложения: бизнес-логика изолирована в domain/services.py, адаптеры (views, почтовые задачи) вызывают её, внешние зависимости скрыты за портами.

### 2.1. Структура проекта

sod_project/
├── ARCHITECTURE.md        # этот файл
├── sod_core/              # настройки Django, URLs, общие утилиты
├── storage/               # ЯДРО
│   ├── domain/
│   │   ├── models.py      # Company, Project, Section, Folder, Document, DocumentVersion, UserProfile, ProjectMembership, DocumentView
│   │   └── services.py    # upload_document(), create_project(), create_folder(), generate_share_link() и др.
│   ├── ports/
│   │   ├── file_storage.py # интерфейс FileStorage: save(), get(), delete()
│   │   └── integration.py  # интерфейс для внешних систем (API Gateway)
│   ├── adapters/
│   │   ├── local_storage.py # реализация порта для локального диска
│   │   ├── tables.py       # django-tables2
│   │   ├── filters.py      # django-filter
│   │   ├── views.py
│   │   └── templates/
│   └── tests/
├── automation/            # автосоздание структуры папок по шаблону
├── issues/                # замечания и исходные данные
├── mail_integration/      # IMAP, правила сортировки, выгрузка вложений
├── ai_assistant/          # MCP-сервер, семантический поиск
├── external_api/          # REST API для экосистемы (ГИП-Ассистент, ВОР-билдер и др.)
└── media/                 # файловое хранилище

### 2.2. Ключевые правила

1. Бизнес-логика — только в domain/services.py. Views, почтовые задачи и ИИ-агент вызывают одни и те же функции.
2. Внешние зависимости — за портами. Хранилище файлов определяется интерфейсом FileStorage. Смена хранилища не требует правок в домене.
3. Модули общаются через сервисы, а не через модели напрямую. mail_integration не вызывает Document.objects.create() — он вызывает storage.domain.services.upload_document().
4. Никаких микросервисов на старте. Все модули в одном процессе Django. Выделение в отдельный сервис — только при реальной нагрузке.
5. ИИ-агент получает доступ к данным через MCP-сервер. Модуль ai_assistant предоставляет инструменты (search_documents, get_document_text, list_issues), а не прямой доступ к БД.

### 2.3. Что НЕ делаем на старте

- Не проектируем все порты заранее. Порт создается, когда появляется вторая реализация.
- Не вводим Celery, Redis, pgvector, пока нет реальной нагрузки.
- Не пишем MCP-сервер, пока ядро не стабильно.
- Не усложняем права до уровня отдельных документов — только разделы, папки и проекты.

## 3. Схема данных ядра storage

### 3.1. Принципы

1. Документ — логическая сущность, версия — физический файл.
2. Текущая версия — флаг, а не отдельная таблица.
3. Права — на уровне раздела (и папки), а не документа.
4. Исходные данные — тип документа, а не отдельная модель.
5. **Мультитенантность:** Каждый Project принадлежит Company, каждый пользователь привязан к Company через UserProfile.company. Все запросы фильтруются по company пользователя. Company — это компания-владелец СОД (проектная организация). Заказчик указывается в поле Project.customer и не является тенантом.
6. **Приватность:** В модели Company есть флаг `is_private`. Если он включен, суперюзер (владелец платформы) не видит проекты и файлы компании (даже для поддержки). Полная изоляция данных от владельца платформы достигается только в On-premise версии (развертывание на сервере клиента).

### 3.2. Модели

#### Company
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| name | CharField(255) | Название компании |
| logo | ImageField(upload_to='company_logos/', null=True, blank=True) | Логотип компании (24px в топбаре) |
| is_private | BooleanField | Флаг приватности (скрывает данные от суперюзера) |
| created_at | DateTimeField | |

#### Project
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| company | FK → Company, on_delete=CASCADE | Принадлежность к компании |
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
| order | IntegerField | Порядок сортировки |
| folder_template | CharField(100), blank | Имя шаблона папок |

**Уникальность:** (project, code).

#### Folder (T8-CORE)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| project | FK → Project, on_delete=CASCADE | Проект |
| section | FK → Section, null=True | Раздел (если папка на уровне раздела) |
| parent | FK → Folder, null=True | Родительская папка (для вложенности) |
| name | CharField(255) | Название папки |
| order | IntegerField | Порядок сортировки |
| zone | CharField(20), choices | WIP, SHARED, PUBLISHED, ARCHIVE (зона жизненного цикла) |
| is_deleted | BooleanField | Мягкое удаление (корзина) |
| deleted_at | DateTimeField, null=True | Дата помещения в корзину |
| created_at | DateTimeField | |
| created_by | FK → User | |

**Правило:** При создании проекта автоматически создаются 4 корневые папки-зоны: «01. В работе», «02. На согласовании», «03. Опубликовано», «04. Архив».

**Связь с Document.status:**
- Перемещение в WIP → status = draft.
- Перемещение в SHARED → status = in_review.
- Перемещение в PUBLISHED → status = approved.
- Перемещение в ARCHIVE → status = archived.

**Права по зонам (check_zone_permission):**
- WIP: developer, contractor (только allowed_sections), gip, admin.
- SHARED: gip, admin.
- PUBLISHED: admin.
- ARCHIVE: только чтение. Перемещать документ в/из ARCHIVE может админ, но загружать новые версии в документ из ARCHIVE запрещено всем, включая админа.

**Правило:** папка может быть на уровне проекта (section=None) или внутри раздела (section=Section). Вложенность через parent.

#### Document
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| project | FK → Project, related_name=documents | |
| section | FK → Section, related_name=documents | |
| folder | FK → Folder, null=True, related_name=documents | Папка внутри раздела |
| code | CharField(100) | Шифр документа, «АР-01-2026» |
| name | CharField(255) | «План 1 этажа» |
| doc_type | CharField(20), choices | drawing, source_data, report, other |
| current_version | FK → DocumentVersion, null=True | Текущая актуальная версия |
| status | CharField(20), choices | WIP, SHARED, PUBLISHED, ARCHIVED (жизненный цикл) |
| public_link_token | UUIDField, null=True, unique=True | Токен публичной ссылки |
| is_deleted | BooleanField | Мягкое удаление (корзина) |
| deleted_at | DateTimeField, null=True | Дата помещения в корзину |
| created_at | DateTimeField | |
| created_by | FK → User | |
| updated_at | DateTimeField | |

**Уникальность:** (project, section, code).

#### DocumentVersion
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| document | FK → Document, related_name=versions | |
| version_number | IntegerField | 1, 2, 3… |
| file | FileField или CharField (путь через порт) | Физический файл |
| file_size | BigIntegerField | Размер в байтах |
| file_hash | CharField(64), blank | SHA-256 |
| comment | TextField, blank | Что изменилось |
| uploaded_at | DateTimeField | |
| uploaded_by | FK → User | |

**Уникальность:** (document, version_number).

#### UserProfile
| Поле | Тип | Описание |
|---|---|---|
| user | OneToOne → User | |
| company | FK → Company, null=True, on_delete=SET_NULL | Компания пользователя |
| role | CharField(20), choices | admin, gip, developer, checker, normocontrol, customer, contractor, viewer |
| allowed_sections | ManyToMany → Section, blank | Разделы, куда можно писать |

**Правила ролей (ГОСТ, T11-ROLES):**
- admin (Администратор) — всё (в рамках своей компании).
- gip (ГИП) — всё в своих проектах.
- developer (Разработчик) — пишет только в allowed_sections (WIP), читает всё в своих проектах.
- checker (Проверяющий) — только чтение, создаёт и закрывает замечания.
- normocontrol (Нормоконтроль) — только чтение, создаёт и закрывает замечания.
- customer (Заказчик) — только чтение файлов, создаёт и закрывает замечания.
- contractor (Смежник) — пишет и читает только разделы из allowed_sections, создаёт замечания.
- viewer (Наблюдатель) — только чтение, замечания не создаёт и не меняет.

**Миграция ролей:** data-миграция `storage.0013` переводит `engineer` → `developer` в `UserProfile` и `ProjectMembership`.

#### ProjectMembership
| Поле | Тип | Описание |
|---|---|---|
| project | FK → Project | |
| user | FK → User | |
| role_in_project | CharField(20) | gip, developer, checker, normocontrol, customer, contractor, viewer |

**Уникальность:** (project, user).

#### DocumentView
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| project | FK → Project | |
| user | FK → User, null=True | NULL = общий вид |
| name | CharField(100) | «Мой вид» |
| config | JSONField | {"columns": [...], "filters": {...}, "sort": [...], "widths": {...}} |
| is_shared | BooleanField | Общий или личный |
| created_at | DateTimeField | |

#### Issue (T11-ROLES)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| document | FK → Document, related_name=issues | Документ |
| version | FK → DocumentVersion, null=True | Версия, к которой относится замечание |
| author | FK → User, PROTECT | Создатель замечания |
| assignee | FK → User, null=True | Ответственный-разработчик |
| description | TextField | Текст замечания |
| status | CharField(20), choices | NEW, IN_PROGRESS, REJECTED, DISCUSSION, DONE, CLOSED, CANCELLED |
| closed_at | DateTimeField, null=True | Дата закрытия/аннулирования |
| closed_by | FK → User, null=True | Кто закрыл/аннулировал |
| page_number | PositiveIntegerField, null=True | Страница PDF |
| x_ratio / y_ratio | FloatField, null=True | Координаты пина (0.0–1.0) |
| created_at / updated_at | DateTimeField | |

**Constraint:** `issue_pin_coords_consistency` — координаты задаются только вместе с `page_number`.

**Матрица статусов (T11-ROLES):**

| Статус | Кто может установить |
|---|---|
| NEW | Создаётся автоматически при создании (checker, customer, normocontrol и др.) |
| IN_PROGRESS | developer |
| DISCUSSION | developer |
| DONE | developer |
| REJECTED | checker, customer, normocontrol |
| CLOSED | checker, customer, normocontrol |
| CANCELLED | checker, customer, normocontrol |

- gip и admin — арбитры: могут установить любой статус.
- contractor и viewer — не могут менять статус.
- При переходе в CLOSED/CANCELLED заполняются `closed_at` и `closed_by`; при возврате в другой статус — очищаются.
- Из CLOSED/CANCELLED может вывести только gip/admin (переоткрытие); developer не может.
- Из DONE developer может перевести только в DISCUSSION или IN_PROGRESS.
- При переходе в IN_PROGRESS, если `assignee` пуст, назначается текущий пользователь.

#### IssueComment / IssueAttachment (Этап 9)
(Модели для обсуждения и вложений в замечаниях. Поля: issue, author, text, created_at / file, filename, uploaded_at).

### 3.3. Связи

User 1─1 UserProfile
Company 1─N UserProfile
Company 1─N Project
Project 1─N Section
Section 1─N Folder
Folder 1─N Folder (parent)
Folder 1─N Document
Document 1─N DocumentVersion
и т.д.

## 4. Контракты сервисов ядра

*(Все сигнатуры фиксированы. Примеры функций:)*

def create_project(company, code, name, customer, gip, created_by) -> Project: ...
def create_folder(user, project, name, section=None, parent=None) -> Folder: ...
def upload_document(user, project, section, code, name, doc_type, file, comment, folder=None) -> DocumentVersion: ...
def move_document(user, document, folder) -> Document: ...
def generate_share_link(user, document) -> str: ...
def delete_document(user, document) -> Document: ... (мягкое удаление)
def restore_document(user, document) -> Document: ...
def empty_trash(user, project, file_storage) -> dict: ... (безвозвратно: FileStorage.delete() + БД)
def can_purge_trash(user, project) -> bool: ... (только admin компании / superuser)
def update_document_status(user, document, new_status) -> Document: ...
def create_issue(user, document, version, description, page_number, x_ratio, y_ratio) -> Issue: ...
def check_issue_permission(user, document) -> bool: ... (все ГОСТ-роли, кроме viewer)
def allowed_issue_statuses(user, issue) -> list[str]: ... (статусы по матрице ролей)
def update_issue_status(user, issue, new_status) -> Issue: ... (матрица переходов, closed_at/closed_by)
def get_all_companies(platform_owner) -> QuerySet[Company]: ... (только для суперюзера)

**Правило:** upload_document() — единственная точка создания версий.

## 5. Порты

### 5.1. FileStorage
class FileStorage(Protocol):
    def save(self, path: str, file) -> str: ...
    def get(self, path: str): ...
    def delete(self, path: str) -> None: ...

### 5.2. ExternalIntegrationPort
Интерфейс для общения с внешними системами (ГИП-Ассистент, ВОР-билдер, Витро-трекер).

## 6. Экосистема и интеграция

СОД (Stream-CAD) выступает центральным хабом (API Gateway) для:
1. **ГИП-Ассистент** (FastAPI/React).
2. **ВОР-билдер** (Python/NiceGUI).
3. **Витро-трекер** (Python/Streamlit).

### 6.1. Мультитенантность и роли (Platform Owner)
1. **Platform Owner (суперюзер Django):** Видит список компаний. Может создавать компании. Видит проекты компании, если `is_private=False`. Может использовать «Импersonation» для отладки.
2. **Company Admin (роль admin):** Видит только свою компанию. Создает проекты, разделы, добавляет участников.
3. **Режимы развертывания:** SaaS (облако Stream-CAD) и On-premise (коробка на сервере клиента).

### 6.2. Контракты API СОД (для внутренних систем)
- `POST /api/v1/projects/` (ГИП-Ассистент)
- `GET /api/v1/projects/{code}/specifications/` (ВОР-билдер)
- `POST /api/v1/projects/{code}/vor/` (ВОР-билдер)
- `POST /api/v1/projects/{code}/issues/sync/` (Витро-трекер)
- `GET /api/v1/projects/{code}/issues/pending/` (Витро-трекер)

### 6.3. Интеграция с SharePoint
- **Маркапы на PDF:** Пины на чертеже (Этап 8).
- **Панель замечаний с чатом:** Слева — документ, справа — замечания (Этап 9).
- **Синхронизация с SharePoint:** Используем библиотеку `httpx` для работы с REST API SharePoint (для импорта файлов и замечаний).

## 7. Правила для ИИ-агента Kilo

1. Следовать структуре из раздела 2.1.
2. Бизнес-логику писать только в domain/services.py.
3. Использовать upload_document() как единственную точку создания версий.
4. Не добавлять Celery, Redis, pgvector, MCP-сервер, пока явно не попросят.
5. Не менять модели из раздела 3.2 без обновления этого файла.
6. Не предлагать микросервисы и альтернативные фреймворки.
7. Не создавать API-эндпоинты напрямую во views — только через сервисы и порты.