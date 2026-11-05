# ARCHITECTURE.md — СОД для проектной компании

**Версия:** 1.5
**Дата:** 2026-11-04
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
| template_key | CharField(20), blank | Ключ шаблона структуры (PD_87 / RD_GOST / FULL) |
| is_deleted | BooleanField | Мягкое удаление проекта (доступно админу/суперюзеру) |
| deleted_at | DateTimeField, null=True | Дата мягкого удаления |
| created_at | DateTimeField | |
| created_by | FK → User | |
| base_code | CharField(50), blank | Базовое обозначение проекта по ГОСТ 21.101 (например, «2345-12») |

#### Section
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| project | FK → Project, related_name=sections | |
| code | CharField(20) | «АР», «КР», «ОВ», «ВК», «ИД» |
| name | CharField(255) | «Архитектурные решения» |
| order | IntegerField | Порядок сортировки |
| folder_template | CharField(100), blank | Имя шаблона папок |
| full_code | CharField(100), blank | Полный шифр раздела (генерируется автоматически): `[base_code]-[code]` (например, «2345-12-АР») |

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
| is_system_zone | BooleanField | True только у 4 системных папок зон; зона определяется полем, а не именем |
| is_deleted | BooleanField | Мягкое удаление (корзина) |
| deleted_at | DateTimeField, null=True | Дата помещения в корзину |
| deleted_with_project | BooleanField | Удалена вместе с проектом (для restore_project) |
| created_at | DateTimeField | |
| created_by | FK → User | |
| full_code | CharField(100), blank | Полный шифр комплекта (генерируется автоматически): `[base_code]-[section.code]-[parent.code]-[code]` (например, «2345-12-АР-01») |

**Правило:** При создании проекта автоматически создаются 4 корневые папки-зоны: «01. В работе», «02. На согласовании», «03. Опубликовано», «04. Архив».

**Связь с Document.status:**
- Перемещение в WIP → status = draft.
- Перемещение в SHARED → status = in_review.
- Перемещение в PUBLISHED → status = approved.
- Перемещение в ARCHIVE → status = archived.

**Права по зонам (check_zone_permission):**- WIP: developer, contractor (только allowed_sections), gip, admin.
- SHARED: gip, admin.
- PUBLISHED: admin.
- ARCHIVE: только чтение. Смена зоны через DnD запрещена всем; переходы выполняются workflow-действиями. Загружать новые версии в документ из ARCHIVE запрещено всем, включая админа.

**Правило:** папка может быть на уровне проекта (section=None) или внутри раздела (section=Section). Вложенность через parent. Шифр вложенной папки включает коды всех родителей: `[base_code]-[section.code]-[parent.code]-[code]`.

**Уникальность:** корневые папки — (project, section, code), вложенные — (project, parent, code). Это позволяет использовать код `01` у папок с разными родителями.

**Хранение зоны:** принадлежность к зоне хранится в `Folder.zone`; `Folder.is_system_zone=True` только у системных папок зон (WIP/SHARED/PUBLISHED/ARCHIVE). Имя папки не используется для определения зоны, поэтому переименование корневой папки не ломает зону.

**Структура проекта:**
При создании проекта создаются:
- 3 структурные корневые папки: «Исходные данные (ИРД)», «ПД (Проектная документация)», «РД (Рабочая документация)»;
- 4 системные папки зон: `01. В работе` (WIP), `02. На согласовании` (SHARED), `03. Опубликовано` (PUBLISHED), `04. Архив` (ARCHIVE), помеченные `is_system_zone=True`.

**Плоская структура ПД/РД:** все папки внутри суперпапок ИРД/ПД/РД создаются в едином скрытом разделе `Section(code='COMMON')`. Раздел `COMMON` не отображается в дереве и фильтрах (`get_sections_with_counts()` его исключает); он нужен только как технический контейнер, чтобы структура папок оставалась плоской, а марка определялась по папке, а не по `Section`.

Системные папки зон скрыты из дерева и используются только workflow-логикой. Зона хранится в `Folder.zone`; переименование папки её не меняет.

**Зоны и share-ссылки:** принадлежность папки к зоне определяется полем `Folder.is_system_zone` (True только у 4 системных папок зон), а не именем. Публичная ссылка на документ хранится в `Document.public_link_token`, а срок её действия — в `Document.public_link_expires_at` (по умолчанию 365 дней); ссылка не отдаёт замечания.

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
| public_link_expires_at | DateTimeField, null=True | Срок действия публичной ссылки (365 дней) |
| is_deleted | BooleanField | Мягкое удаление (корзина) |
| deleted_at | DateTimeField, null=True | Дата помещения в корзину |
| deleted_with_project | BooleanField | Удалён вместе с проектом (для restore_project) |
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
| external_id | CharField(100), null=True, db_index=True | ID замечания во внешней системе (Витро-трекер) для идемпотентной синхронизации |
| closed_at | DateTimeField, null=True | Дата закрытия/аннулирования |
| closed_by | FK → User, null=True | Кто закрыл/аннулировал |
| page_number | PositiveIntegerField, null=True | Страница PDF |
| x_ratio / y_ratio | FloatField, null=True | Координаты пина (0.0–1.0) |
| created_at / updated_at | DateTimeField | |
| x1_ratio / y1_ratio | FloatField, null=True | Координаты левого верхнего угла рамки маркапа |
| x2_ratio / y2_ratio | FloatField, null=True | Координаты правого нижнего угла рамки маркапа |
| crop_image | ImageField, null=True | Вырезанное изображение маркапа |
| department | CharField(255), blank | Подразделение автора |
| project_organization | CharField(255), blank | Проектная организация |
| due_date | DateField, null=True | Срок ответа на замечание |
| actual_fix_date | DateField, null=True | Фактическая дата исправления |
| revision | CharField(100), blank | Ревизия документа |
| answer_text | TextField, blank | Ответ разработчика (если без комментария) |

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

#### Specification (ВОР-билдер, 4D)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| project | FK → Project, related_name='specifications', CASCADE | Проект |
| section_code | CharField(20), blank | Код раздела |
| code | CharField(100) | Код строки |
| name | CharField(255) | Наименование |
| unit | CharField(20), blank | Единица измерения |
| quantity | DecimalField(10, 2) | Количество |
| note | TextField, blank | Примечание |
| created_at | DateTimeField, auto_now_add | |

**Уникальность:** `(project, section_code, code)`.

#### VorItem (ВОР-билдер, 4D)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| project | FK → Project, related_name='vor_items', CASCADE | Проект |
| section_code | CharField(20), blank | Код раздела |
| code | CharField(100) | Код строки |
| name | CharField(255) | Наименование |
| unit | CharField(20), blank | Единица измерения |
| quantity | DecimalField(10, 2) | Количество |
| created_at | DateTimeField, auto_now_add | |

**Уникальность:** `(project, section_code, code)`.

#### IssueComment
| Поле | Тип | Описание |
|---|---|---|
| issue | FK → Issue, related_name='comments' | Замечание |
| author | FK → User | Автор комментария |
| text | TextField | Текст комментария |
| created_at | DateTimeField, auto_now_add | Дата создания |

`IssueAttachment` не создавался: маркап хранится в `Issue.crop_image`.

#### Notification (Волна 3)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| recipient | FK → User, related_name='notifications' | Получатель уведомления |
| actor | FK → User, null=True | Инициатор действия |
| message | CharField(255) | Текст уведомления |
| url | CharField(255) | Ссылка для перехода |
| is_read | BooleanField | Флаг прочтения |
| created_at | DateTimeField | |

#### Feedback (подготовка к бете)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| company | FK → Company, related_name='feedback_items' | Компания (мультитенантность) |
| author | FK → User, null=True, on_delete=SET_NULL | Автор обращения; при удалении пользователя запись сохраняется |
| category | CharField(20), choices | BUG, UX, IDEA, OTHER |
| message | TextField | Что случилось |
| page_url | CharField(500), blank | URL страницы, где возникла проблема |
| contact_email | EmailField, blank | Контакт для связи; при пустом значении подставляется email пользователя |
| screenshot | ImageField, null=True, blank | Скриншот, `upload_to='feedback/screenshots/%Y/%m/'` |
| status | CharField(20), choices | NEW, IN_PROGRESS, RESOLVED, REJECTED |
| admin_comment | TextField, blank | Комментарий администратора |
| created_at | DateTimeField, auto_now_add | Дата создания |
| updated_at | DateTimeField, auto_now | Дата обновления |

**Индекс:** `(company, status, -created_at)` — для выборки в экране администратора.
**Сортировка:** `-created_at`.

#### ApiToken (API Gateway, 4A)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| company | FK → Company, on_delete=CASCADE, related_name='api_tokens' | Компания-владелец токена |
| name | CharField(100) | Название (для кого выдан) |
| token_prefix | CharField(8) | Первые 8 символов токена — только для поиска/отображения |
| token_hash | CharField(64), unique | SHA-256 хэш токена; сырое значение в БД не хранится |
| scopes | JSONField, default=list | `projects:create`, `projects:read`, `issues:sync`, `issues:read` |
| is_active | BooleanField | Отзыв токена |
| expires_at | DateTimeField, null=True | Срок действия; пусто — бессрочный |
| created_at | DateTimeField, auto_now_add | |
| created_by | FK → User, on_delete=SET_NULL, null=True | Создатель; при аутентификации становится `request.user` |
| last_used_at | DateTimeField, null=True | Обновляется при каждом успешном запросе |

**Индекс:** `(company, is_active)`.
**Сортировка:** `-created_at`.
**Правило:** сырой токен (`secrets.token_urlsafe(48)`) показывается один раз при создании; в БД хранятся только `token_prefix` и `token_hash`.

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
def export_pdf_with_issues(user, document) -> bytes: ... (PDF с маркапами через PyMuPDF)
def add_issue_comment(user, issue, text) -> IssueComment | None: ... (проверка чтения и роли)
def delete_issue_crop(user, issue) -> Issue: ... (удаление маркапа с проверкой прав)
def get_all_companies(platform_owner) -> QuerySet[Company]: ... (только для суперюзера)
def submit_feedback(user, company, message, category="BUG", page_url="", contact_email="", screenshot=None, file_storage=None) -> Feedback: ... (валидация message/category/email; скриншот через FileStorage)
def list_feedback(user, company, status=None, category=None) -> QuerySet[Feedback]: ... (admin компании / superuser; фильтры status/category)
def update_feedback_status(user, feedback, new_status, admin_comment="") -> Feedback: ... (admin компании feedback / superuser; new_status in Status.values; admin_comment ≤ 2000)
def create_api_token(user, company, name, scopes, expires_at=None) -> tuple[ApiToken, str]: ... (admin компании / superuser; валидные scopes; возвращает сырой токен один раз)
def revoke_api_token(user, token) -> None: ... (admin компании токена / superuser; is_active=False)
def list_api_tokens(user, company) -> QuerySet[ApiToken]: ... (admin компании / superuser; только токены своей компании)

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
- `POST /api/v1/projects/` (ГИП-Ассистент) — реализовано в 4A
- `GET /api/v1/projects/{code}/specifications/` (ВОР-билдер)
- `POST /api/v1/projects/{code}/vor/` (ВОР-билдер)
- `POST /api/v1/projects/{code}/issues/sync/` (Витро-трекер)
- `GET /api/v1/projects/{code}/issues/pending/` (Витро-трекер)

**4C/4D — реализованные контракты:**
- `GET /api/v1/projects/{code}/specifications/` — scope `projects:read`; возвращает строки `Specification` проекта (id, code, name, section_code, unit, quantity, note).
- `POST /api/v1/projects/{code}/vor/` — scope `projects:create`; создаёт строки `VorItem`; возвращает `{"created": N, "items": [...]}`. Требует admin/gip компании/проекта.
- `POST /api/v1/projects/{code}/issues/sync/` — scope `issues:sync`; принимает `external_id` (опционально), `document_code`, `issue_text`, `status`, `page_number`, `x_ratio`, `y_ratio`. При наличии `external_id` замечание ищется по нему в рамках проекта и обновляется, включая текст; без `external_id` — fallback по `document + description`.
- `GET /api/v1/projects/{code}/issues/pending/` — scope `issues:read`; возвращает замечания в статусах `NEW`, `IN_PROGRESS`, `REJECTED`, включая `external_id` и `assignee_username`.

**Аутентификация API (4A):**
- Заголовок `Authorization: Bearer <token>`. Токен ищется по SHA-256 хэшу, проверяются `is_active`, `expires_at` и активность пользователя-создателя.
- `request.user` = создатель токена (проверка обычных прав), `request.auth` = `ApiToken` (проверка scopes).
- Обязательный scope для создания проекта — `projects:create`.
- Компания проекта берётся из `ApiToken.company`; параметр `company` в теле запроса игнорируется (изоляция компаний).
- Ошибки возвращаются в конверте `{"errors": {...}}`: 401 — нет/невалидный/истёкший токен, 403 — нет scope, 400 — ошибка валидации.

**`POST /api/v1/projects/` — тело запроса:**
```json
{
  "code": "TEST-API-01",
  "name": "Тест API",
  "customer": "ООО Тест",
  "base_code": "2345-12",
  "template_key": "FULL"
}
```
`code` и `name` обязательны; `customer` и `base_code` — опциональны; `template_key` — `PD_87`, `RD_GOST` или `FULL` (по умолчанию `FULL`).
Успех — `201 {"id": 1, "code": "TEST-API-01", "name": "Тест API"}`. Дубликат `code` — 400.

**Управление токенами (UI, 4A):**
- `GET /api-tokens/` — список токенов компании (admin / superuser).
- `POST /api-tokens/create/` — создание; сырой токен показывается один раз.
- `POST /api-tokens/<id>/revoke/` — отзыв.

### 6.3. Интеграция с SharePoint
- **Маркапы на PDF:** Пины на чертеже (Этап 8).
- **Панель замечаний с чатом:** Слева — документ, справа — замечания (Этап 9).
- **Синхронизация с SharePoint:** Используем библиотеку `httpx` для работы с REST API SharePoint (для импорта файлов и замечаний).

### 6.4. Вдохновение от Nextcloud (Будущие улучшения)
Хотя Nextcloud является универсальной платформой, а Stream-CAD — специализированной СОД, мы можем позаимствовать некоторые архитектурные и UX-решения:

1. **WebDAV как порт для файлового доступа:**
   - В будущем можно реализовать `WebDAVAdapter` (порт `FileStorage`), который позволит подключаться к папкам проекта как к сетевому диску. Это не заменит текущий `LocalStorage`, а дополнит его.
2. **File Drop (анонимная загрузка):**
   - Расширение порта `ExternalIntegrationPort`: метод `upload_anonymous_file(token, file)` для загрузки файлов по публичной ссылке без аутентификации.
3. **Лента активности (Event Sourcing Light):**
   - Можно добавить модель `DocumentEvent` (или `AuditLog`) для логирования действий с документом (загрузка, смена статуса, комментарий). Это не полноценный Event Sourcing, а «лайт»-версия для отображения истории.

**Ограничение:** Мы не копируем функционал Nextcloud (чаты, календари). Только те функции, которые усиливают нашу специализацию (файлы, версии, замечания).

## 7. Правила для ИИ-агента Kilo

1. Следовать структуре из раздела 2.1.
2. Бизнес-логику писать только в domain/services.py.
3. Использовать upload_document() как единственную точку создания версий.
4. Не добавлять Celery, Redis, pgvector, MCP-сервер, пока явно не попросят.
5. Не менять модели из раздела 3.2 без обновления этого файла.
6. Не предлагать микросервисы и альтернативные фреймворки.
7. Не создавать API-эндпоинты напрямую во views — только через сервисы и порты.

### 7.1. Дизайн-система и токены (по итогам аудита)
**Принцип:** Один смысл — один цвет. Статусные цвета одинаковы во всех контекстах: в панели замечаний, в бейджах таблиц, в маркерах на листе PDF и в экспорте PyMuPDF.

**Источник правды:** `tokens.json` (формат W3C Design Tokens). Из него генерируются:
- `tokens.css` — CSS-переменные для Bootstrap 5.
- `status_map.js` — карта статусов для PDF.js (замена `STATUS_COLORS`).
- `status_palette.py` — палитра для PyMuPDF (чтобы экспорт PDF с маркапами использовал те же цвета).

**Правило:** Если нужно изменить цвет статуса — меняется только `tokens.json`, и это разъезжается по всем средам одним коммитом.

**Стек:** Bootstrap 5 + ванильный JS + HTMX. Никаких SPA.
