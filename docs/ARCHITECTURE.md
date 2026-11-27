# ARCHITECTURE.md — СОД для проектной компании

**Версия:** 1.9
**Дата:** 2026-11-27
**Статус:** Принято, обязательно к соблюдению при генерации кода. 1.7 — решение владельца от 27.11.2026: размещение (папка) и этап жизненного цикла — независимые атрибуты документа; перетаскивание меняет только размещение. 1.8 — решение владельца от 27.11.2026 (вариант 1): повторная загрузка версии не меняет этап жизненного цикла; этап меняется только через `transition_document_zone()`. 1.9 — решение владельца от 27.11.2026: копирование документов СОД→СОД отсутствует (документ един и живёт один жизненный цикл; download — выгрузка, не копия); термин «зона» в пользовательских строках UI заменён на «этап».

## 1. Контекст

Небольшая проектная компания (25 человек). Своя СОД на Python/Django. Разработка — 1 человек + ИИ-агент Kilo в PyCharm. Бюджет ограничен. Цель — порядок в файлах, версионность, права, а в будущем — почта, замечания, ИИ-поиск. Система развернута в production: https://stream-cad.ru/ (SaaS).

## 2. Архитектурное решение

Модульный монолит на Django с «гексагон лайт» внутри каждого приложения: бизнес-логика изолирована в domain/services.py, адаптеры (views, почтовые задачи) вызывают её, внешние зависимости скрыты за портами.

### 2.1. Структура проекта

sod_project/
├── docs/
│   └── ARCHITECTURE.md    # этот файл
├── sod_core/              # настройки Django, URLs, общие утилиты
├── storage/               # ЯДРО
│   ├── domain/
│   │   ├── models.py      # Company, Project, Section, Folder, Document, DocumentVersion, UserProfile, ProjectMembership, ChangePermission, DocumentView
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
├── issues/                # замечания и исходные данные
├── ai_checks/             # ИИ-контур (Этап 19, версия 1.6): правила, внутренние знания, прогоны проверок
├── external_api/          # REST API для экосистемы (ГИП-Ассистент, ВОР-билдер и др.)
├── help/                  # вкладка «Помощь»: статьи из docs/help/*.md (без моделей, раздел 9)
└── media/                 # runtime-каталог файлового хранилища (создаётся при работе, не элемент репо)

### 2.2. Ключевые правила

1. Бизнес-логика — только в domain/services.py. Views, почтовые задачи и ИИ-агент вызывают одни и те же функции.
2. Внешние зависимости — за портами. Хранилище файлов определяется интерфейсом FileStorage. Смена хранилища не требует правок в домене.
3. Модули общаются через сервисы, а не через модели напрямую. mail_integration не вызывает Document.objects.create() — он вызывает storage.domain.services.upload_document().
4. Никаких микросервисов на старте. Все модули в одном процессе Django. Выделение в отдельный сервис — только при реальной нагрузке.
5. ИИ-агент получает доступ к данным через MCP-сервер. Модуль ai_assistant предоставляет инструменты (search_documents, get_document_text, list_issues), а не прямой доступ к БД.

### 2.3. Что НЕ делаем на старте

- Не проектируем все порты заранее. Порт создается, когда появляется вторая реализация.
- Не вводим Celery, Redis, pgvector, пока нет реальной нагрузки (исключение с 1.6: ИИ-контур, Этап 19 — раздел 8; вне ИИ-контура правило сохраняется).
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
| zone | CharField(20), choices | WIP, SHARED, PUBLISHED, ARCHIVE (этап жизненного цикла системной папки) |
| is_system_zone | BooleanField | True только у 4 системных папок этапов жизненного цикла; этап папки определяется полем, а не именем |
| is_deleted | BooleanField | Мягкое удаление (корзина) |
| deleted_at | DateTimeField, null=True | Дата помещения в корзину |
| deleted_with_project | BooleanField | Удалена вместе с проектом (для restore_project) |
| created_at | DateTimeField | |
| created_by | FK → User | |
| full_code | CharField(100), blank | Полный шифр комплекта (генерируется автоматически): `[base_code]-[section.code]-[parent.code]-[code]` (например, «2345-12-АР-01») |

**Правило:** При создании проекта автоматически создаются 4 корневые системные папки этапов жизненного цикла: «01. В работе», «02. На согласовании», «03. Опубликовано», «04. Архив».

**Размещение и этап жизненного цикла — независимые атрибуты документа (решение владельца 27.11.2026):**
- `Document.folder` определяет размещение документа, `Document.state` — этап жизненного цикла (проекции: `status`, `suitability_code`, `internal_status`, `zone`).
- Перемещение между папками и разделами (`move_document` / `move_document_to_section`, DnD) меняет только размещение и не меняет этап или статус.
- Переход между этапами (WIP, SHARED, PUBLISHED, ARCHIVE) выполняется только явным workflow-действием (`transition_document_zone`: «Подать на согласование», «Вернуть в работу», «Утвердить», «В архив»). Workflow-действие может перенести документ в системную папку целевого этапа.
- **Повторная загрузка версии не меняет этап жизненного цикла.** `upload_document` для существующего документа сохраняет `Document.state` (и производные `status`/`suitability_code`/`internal_status`/`zone`). Это не исключение из правила: этап меняется только `transition_document_zone()`. Повторная загрузка разрешена по действующим правам на целевую папку; размещение и этап независимы. Повторная загрузка версии не меняет размещение документа: документ остаётся в текущей папке, целевая папка влияет только на право загрузки; размещение меняется только перетаскиванием (`move_document`).
- DnD в системную папку этапа запрещён: этап меняется только workflow-действием.
- Права на перемещение (размещение) проверяются по текущему этапу документа; само перемещение этап не меняет.
- **Копирование документов СОД→СОД отсутствует** (решение владельца 27.11.2026): документ един и живёт один жизненный цикл проекта; дубли в системе недопустимы. Выгрузка файла на локальный компьютер — отдельная функция download, а не копия сущности.

**Права по этапам жизненного цикла (check_zone_permission):**
- WIP: developer, contractor (только allowed_sections), gip, admin.
- SHARED: gip, admin.
- PUBLISHED: admin.
- ARCHIVE: только чтение. Переходы этапа выполняются workflow-действиями; DnD этап не меняет. Загружать новые версии в документ из ARCHIVE запрещено всем, включая админа.

**Правило:** папка может быть на уровне проекта (section=None) или внутри раздела (section=Section). Вложенность через parent. Шифр вложенной папки включает коды всех родителей: `[base_code]-[section.code]-[parent.code]-[code]`.

**Уникальность:** корневые папки — (project, section, code), вложенные — (project, parent, code). Это позволяет использовать код `01` у папок с разными родителями.

**Хранение этапа жизненного цикла:** принадлежность системной папки к этапу хранится в `Folder.zone`; `Folder.is_system_zone=True` только у системных папок этапов (WIP/SHARED/PUBLISHED/ARCHIVE). Имя папки не используется для определения этапа, поэтому переименование корневой папки не ломает этап.

**Структура проекта:**
При создании проекта создаются:
- 3 структурные корневые папки: «Исходные данные (ИРД)», «ПД (Проектная документация)», «РД (Рабочая документация)»;
- 4 системные папки этапов жизненного цикла: `01. В работе` (WIP), `02. На согласовании` (SHARED), `03. Опубликовано` (PUBLISHED), `04. Архив` (ARCHIVE), помеченные `is_system_zone=True`.

**Плоская структура ПД/РД:** все папки внутри суперпапок ИРД/ПД/РД создаются в едином скрытом разделе `Section(code='COMMON')`. Раздел `COMMON` не отображается в дереве и фильтрах (`get_sections_with_counts()` его исключает); он нужен только как технический контейнер, чтобы структура папок оставалась плоской, а марка определялась по папке, а не по `Section`.

Системные папки этапов жизненного цикла скрыты из дерева и используются только workflow-логикой. Этап хранится в `Document.state`; принадлежность системной папки к этапу — в `Folder.zone`; переименование папки этап не меняет.

**Этапы жизненного цикла и share-ссылки:** принадлежность папки к этапу определяется полем `Folder.is_system_zone` (True только у 4 системных папок этапов), а не именем. Публичная ссылка на документ хранится в `Document.public_link_token`, а срок её действия — в `Document.public_link_expires_at` (по умолчанию 365 дней); ссылка не отдаёт замечания.

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
| annulled_at | DateTimeField, null=True | Дата аннулирования по РВИ (W4-2); отличается от корзины |
| annulled_by | FK → User, null=True, SET_NULL | Кто аннулировал |
| annulment_permission | FK → ChangePermission, null=True, SET_NULL | РВИ — основание аннулирования |
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
| reason_type / reason_text / affected_sheets | CharField/TextField | Причина версии (SPEC 2.0 w0) |
| change_permission | FK → ChangePermission, null=True, SET_NULL | РВИ — основание изменения версии (W4-2) |
| change_number | PositiveIntegerField, null=True | Номер изменения (графа 4), автоназначение по документу |
| uploaded_at | DateTimeField | |
| uploaded_by | FK → User | |

**Уникальность:** (document, version_number).

#### UserProfile
| Поле | Тип | Описание |
|---|---|---|
| user | OneToOne → User | |
| company | FK → Company, null=True, on_delete=SET_NULL | Компания пользователя |
| role | CharField(20), choices | admin, manager, gip, developer, checker, normocontrol, customer, contractor, viewer |
| allowed_sections | ManyToMany → Section, blank | Разделы, куда можно писать |

**Правила ролей (ГОСТ, T11-ROLES):**
- admin (Администратор) — всё (в рамках своей компании).
- manager (Руководитель) — системная роль организации (назначается владельцем/администратором компании): утверждение РВИ, чтение документов организации, создание замечаний.
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

#### ChangePermission (РВИ, Волна 4)

Разрешение на внесение изменений — отдельная сущность по формам 9 и 9а приложения К ГОСТ Р 21.101-2026. Бизнес-логика — `storage/domain/services.py` (черновик, утверждение, реестр, расходование).

| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| code | CharField(50), null=True, blank=True | Обозначение РВИ (графа 1), уникально в рамках организации; выдаёт реестр при первом утверждении, в черновике пусто |
| project | FK → Project, related_name=change_permissions | Проект (организация — `project.company`) |
| status | CharField(20), choices | draft, approved, annulled |
| content | TextField | Содержание изменения (графа 6) |
| reason_code | CharField(1), choices 1..5, blank | Код причины по таблице К.1 (графа 7): 1 «Усовершенствования», 2 «Изменение стандартов и норм», 3 «Дополнительные требования заказчика», 4 «Устранение ошибок», 5 «Другие причины»; пусто = прочерк |
| note | TextField, blank | Примечание (графа 8) |
| author | FK → User, PROTECT | Составил (ГИП) |
| approved_by | FK → User, null=True, SET_NULL | Утвердил |
| approved_at | DateTimeField, null=True | Дата утверждения |
| created_at / updated_at | DateTimeField | |

**Правила:** черновик редактирует ГИП; утверждённое РВИ неизменяемо (опечатка — выпуск нового разрешения). Утверждает Руководитель организации; если все документы вне архива — также ГИП проекта. Утверждающий вычисляется в момент действия и не хранится.

#### ChangePermissionDocument (РВИ, Волна 4)

| Поле | Тип | Описание |
|---|---|---|
| permission | FK → ChangePermission, related_name=document_links | РВИ |
| document | FK → Document, related_name=change_permission_links | Документ |
| change_number | PositiveIntegerField | Номер изменения для документа (графа 4), автоназначение: max по утверждённым/аннулированным связям этого документа + 1 |
| sheets | CharField(255), blank | Список листов или «Все» (графа 5) |
| applied_at | DateTimeField, null=True | Изменение израсходовано (W4-2) |
| applied_action | CharField(10), choices, blank | Чем израсходовано: version / unarchive / annulment |

**Семантика расхода (W4-2):** одно изменение расходуется одним действием. Вывод из архива расходует изменение сразу; последующая загрузка версии по тому же РВИ допускается без повторного расхода. Аннулирование документа расходует изменение отдельным действием.

**Уникальность:** (permission, document). После утверждения РВИ допускается только заполнение `applied_at` и `applied_action`.

#### ChangePermissionStatusLog (РВИ, Волна 4)

История статусов карточки РВИ (черновик → утверждено → аннулировано): `permission` (FK, related_name=status_logs), `old_status`/`new_status`, `changed_by`, `comment`, `created_at`. Записи только добавляются.

#### ChangePermissionRegistry (РВИ, Волна 4)

| Поле | Тип | Описание |
|---|---|---|
| organization | OneToOne → Company, related_name=change_permission_registry | Организация реестра |
| last_number | PositiveIntegerField | Последний сквозной номер |

Счётчик блокируется строкой реестра (`select_for_update`) при утверждении РВИ; формат обозначения — «номер-год» (уточняется по ГОСТ Р 21.1003).

**Точки срабатывания РВИ (W4-2):** повторная загрузка версии в документ SHARED (итог A/B), PUBLISHED или ARCHIVE требует утверждённое РВИ с нерасходованным изменением; WIP правится свободно. Вывод из архива (ARCHIVE → WIP, ГИП/админ) и аннулирование документа выполняются только по такому РВИ и расходуют изменение. Расход блокируется `select_for_update` в одной транзакции с действием.

**UI раздела «РВИ» (W4-2):** `/rvi/` — список с фильтрами по проекту и статусу и сортировкой по обозначению; `/rvi/<id>/` — карточка форм 9/9а; создание — ГИП, утверждение — по матрице W4-1. Руководитель видит РВИ организации, ГИП — своих проектов, viewer и не-участники доступа не имеют. Печатная форма 9/9а и аннулирование РВИ — W4-3.

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

Модели ИИ-контура (Этап 19, версия 1.6) добавляются только приложением `ai_checks` и только добавлением — существующие модели выше не изменяются:

#### InternalDocument (ИИ-контур)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| tenant_scope | CharField(10), choices | global, company, project (по умолчанию company) |
| company | FK → Company, null=True, on_delete=CASCADE | Обязательна для scope=company |
| project | FK → Project, null=True, on_delete=CASCADE | Обязателен для scope=project; company наследуется от проекта |
| source_document | FK → Document, on_delete=CASCADE, related_name='internal_docs' | Исходный PDF из документооборота |
| doc_type | CharField(30), choices | customer_req, past_issues, internal_standard, other |
| title | CharField(255) | Название |
| is_active | BooleanField | Исключить из индекса без удаления |
| embedding_model | CharField(100), blank | Версия модели эмбеддингов на момент индексации |
| indexed_at | DateTimeField, null=True | Время последней успешной индексации |
| created_by / created_at / updated_at | — | Аудит |

#### InternalChunk (ИИ-контур)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| document | FK → InternalDocument, on_delete=CASCADE, related_name='chunks' | Родитель; tenant наследуется |
| seq | PositiveIntegerField | Порядок фрагмента; UniqueConstraint (document, seq) |
| text | TextField | Текст фрагмента |
| page_from / page_to | PositiveIntegerField, null=True | Привязка к страницам PDF (PyMuPDF) |
| embedding | VectorField(1024) | bge-m3 dense; индекс HNSW (cosine_ops) |

#### RuleDefinition (ИИ-контур)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| code | CharField(100), unique | Машиночитаемый код, «SPDS.STAMP.HEIGHT» |
| title | CharField(255) | Краткое название |
| body | TextField | Формулировка правила |
| rule_kind | CharField(10), choices | regex, keyword, manual |
| pattern | JSONField, null=True | Regex или ключевые слова |
| category | CharField(50), blank | Группа (рамка, штамп, шрифты, …) |
| severity | CharField(10), choices | blocker, major, minor, info |
| is_active / version | BooleanField / PositiveIntegerField | Актуальность и версия формулировки |
| tenant_scope / company / project | как у InternalDocument, по умолчанию global | В MVP правила глобальные; поля заложены сразу |
| created_by / created_at / updated_at | — | Аудит |

#### CheckRun (ИИ-контур)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| document_version | FK → DocumentVersion, on_delete=PROTECT, related_name='check_runs' | Объект проверки |
| initiated_by | FK → User, on_delete=PROTECT | Инициатор |
| status | CharField(10), choices | queued, running, done, failed, canceled |
| engine_version | CharField(50) | Версия набора правил и промптов («rules@2026.11.1») |
| rules_total / findings_total | PositiveIntegerField, default 0 | Статистика прогона |
| meta | JSONField, default=dict | Параметры прогона (включенные правила и пр.) |
| started_at / finished_at / created_at | DateTimeField | Тайминги |

**Мультитенантность прогона:** company/project наследуются от document_version → document → project; собственных tenant-полей у CheckRun нет.

#### PendingCheckResult (ИИ-контур)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| check_run | FK → CheckRun, on_delete=CASCADE, related_name='results' | Прогон |
| rule | FK → RuleDefinition, on_delete=PROTECT, null=True | Правило-источник; NULL — для находок LLM (Ф3+) |
| status | CharField(10), choices | pending, confirmed, rejected, edited |
| severity | CharField(10), choices | Снимок критичности на момент прогона |
| confidence | DecimalField(3,2), null=True | Уверенность (LLM-этапы; для Ф1 — NULL) |
| page | PositiveIntegerField, null=True | Страница листа |
| bbox | JSONField, null=True | Координаты [x0, y0, x1, y1] (PyMuPDF) |
| message | TextField | Что найдено |
| evidence | TextField, blank | Цитата-основание |
| model_name / prompt_version | CharField(100), blank | Модель и версия промпта, породившие находку |
| llm_call | FK → LLMCallLog, null=True, on_delete=SET_NULL | Связь с журналом вызова |
| reviewed_by | FK → User, null=True, on_delete=SET_NULL | Кто провалидировал |
| reviewed_at | DateTimeField, null=True | Когда провалидировал |

#### LLMCallLog (ИИ-контур)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| provider | CharField(50) | Провайдер («openrouter») |
| model | CharField(100) | Модель («glm-5.3-flash») |
| purpose | CharField(30) | check_explain, rag_answer, gip_report |
| prompt_tokens / completion_tokens | PositiveIntegerField, default 0 | Расход токенов |
| cost_est | DecimalField(10,4), null=True | Оценка стоимости, USD |
| latency_ms | PositiveIntegerField, null=True | Задержка |
| status | CharField(10), choices | ok, error |
| error_message | TextField, blank | Текст ошибки |
| prompt_version | CharField(50), blank | Версия промпта |
| check_run | FK → CheckRun, null=True, on_delete=SET_NULL | Привязка к прогону |
| user | FK → User, null=True, on_delete=SET_NULL | Инициатор |
| created_at | DateTimeField | |

**Правило:** тексты промптов и ответов в БД не хранятся (приватность компаний) — только метаданные; детальные логи — в лог-файле приложения.

#### TaskTemplate (модуль «Задания», Этап 1 — DESIGN_TASKS_SPEC.md 2.4)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| company | FK → Company, related_name='task_templates', CASCADE | Шаблон компании |
| source_section | FK → Section, null=True, on_delete=SET_NULL | Типовой раздел-источник |
| target_section | FK → Section, null=True, on_delete=SET_NULL | Типовой раздел-цель |
| title | CharField(255) | Название шаблона |
| description | TextField | Типовой текст задания |
| default_duration_days | PositiveIntegerField, default=5 | Типовой срок (дней) |
| checklist | JSONField, default=list | JSON-массив строк чек-листа |
| is_active | BooleanField, default=True | |

**Уникальность:** `(company, title)`.

#### DesignTask (модуль «Задания», Этап 1 — DESIGN_TASKS_SPEC.md 2.1)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| project | FK → Project, related_name='design_tasks', CASCADE | Проект |
| source_section | FK → Section, related_name='design_tasks_source', PROTECT | Раздел-источник (АР) |
| target_section | FK → Section, related_name='design_tasks_target', PROTECT | Раздел-цель (КР) |
| author | FK → User, related_name='issued_design_tasks', PROTECT | Инициатор из отдела-источника (не обязательно ГИП) |
| assignee | FK → User, null=True, on_delete=SET_NULL | Исполнитель |
| template | FK → TaskTemplate, null=True, on_delete=SET_NULL | Шаблон, по которому создано |
| title | CharField(255) | Краткое название |
| description | TextField | Суть задания |
| due_date | DateField, null=True | Срок |
| status | CharField(20), choices, default=DRAFT | DRAFT, ISSUED, IN_PROGRESS, ON_HOLD, COMPLETED, REVIEW, ACCEPTED, RETURNED, CANCELLED |
| priority | CharField(10), choices, default=normal | low, normal, high, critical |
| issued_at / accepted_at | DateTimeField, null=True | Когда выдано / принято |
| closed_by | FK → User, null=True, on_delete=SET_NULL | Кто закрыл |
| external_id | CharField(100), null=True, db_index | Синхронизация с ГИП-Ассистентом (спека 7) |
| created_at / updated_at | DateTimeField | |

**Индекс:** `(project, source_section, target_section, title, created_at)` — вкладка «Задания» проекта. Матрица переходов статусов — в сервисе `update_task_status` (Этап 2).

#### DesignTaskStatusLog (модуль «Задания», Этап 1 — DESIGN_TASKS_SPEC.md 2.2)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| task | FK → DesignTask, related_name='status_logs', CASCADE | Задание |
| old_status / new_status | CharField(20) | Значения DesignTask.Status |
| changed_by | FK → User, PROTECT | Кто изменил |
| comment | TextField, blank | Причина (при возврате/уточнении) |
| created_at | DateTimeField, auto_now_add | |

#### DesignTaskAttachment (модуль «Задания», Этап 1 — DESIGN_TASKS_SPEC.md 2.3)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| task | FK → DesignTask, related_name='attachments', CASCADE | Задание |
| document | FK → Document, null=True, on_delete=SET_NULL | Привязка к документу проекта; связь теряется, вложение остаётся |
| file | FileField, null=True, `upload_to='design_tasks/attachments/%Y/%m/'` | Свободный файл |
| kind | CharField(20), choices | input, output, comment |
| uploaded_by | FK → User, PROTECT | Кто загрузил |
| created_at | DateTimeField, auto_now_add | |

#### DesignTaskChecklistItem (модуль «Задания», Этап 1 — DESIGN_TASKS_SPEC.md 2.5)
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| task | FK → DesignTask, related_name='checklist_items', CASCADE | Задание |
| text | CharField(500) | Пункт |
| is_done | BooleanField, default=False | |
| done_by | FK → User, null=True, on_delete=SET_NULL | Кто выполнил |
| done_at | DateTimeField, null=True | Когда выполнил |

**Без уникальности:** одно задание может содержать несколько одинаковых пунктов.

**Планируется (S3-sync, `docs/S3_SYNC_PLAN.md` §3.3.3; NOT IMPLEMENTED):** в `RfiTask` — поля `cause_design_task` (FK → DesignTask, null=True) и `linked_tasks` (связь «RFI блокирует задания» с DesignTask). Требуется миграция; до неё контракт `rfis.json` выдаёт `null` / `[]`.

### 3.3. Связи

User 1─1 UserProfile
Company 1─N UserProfile
Company 1─N Project
Project 1─N Section
Section 1─N Folder
Folder 1─N Folder (parent)
Folder 1─N Document
Document 1─N DocumentVersion
Document 1─N DesignTaskAttachment (design_task_attachments, «Задания»)
Company 1─N TaskTemplate (task_templates, «Задания»)
Project 1─N DesignTask (design_tasks, «Задания»)
DesignTask 1─N DesignTaskStatusLog / DesignTaskAttachment / DesignTaskChecklistItem
Document 1─N InternalDocument (source_document, ИИ-контур)
InternalDocument 1─N InternalChunk
DocumentVersion 1─N CheckRun
CheckRun 1─N PendingCheckResult
и т.д.

## 4. Контракты сервисов ядра

*(Все сигнатуры фиксированы. Примеры функций:)*

def create_project(company, code, name, customer, gip, created_by) -> Project: ...
def create_folder(user, project, name, section=None, parent=None) -> Folder: ...
def upload_document(user, project, section, code, name, doc_type, file, comment, folder=None, ..., change_permission=None) -> DocumentVersion: ...
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
def update_feedback_status(user, feedback, new_status, admin_comment=None) -> Feedback: ... (admin компании feedback / superuser; new_status in Status.values; admin_comment ≤ 2000; None — не менять, "" — очистить)
def create_api_token(user, company, name, scopes, expires_at=None) -> tuple[ApiToken, str]: ... (admin компании / superuser; валидные scopes; возвращает сырой токен один раз)
def revoke_api_token(user, token) -> None: ... (admin компании токена / superuser; is_active=False)
def list_api_tokens(user, company) -> QuerySet[ApiToken]: ... (admin компании / superuser; только токены своей компании)
def _clear_profile_cache(user) -> None: ... (внутренний хелпер: сбрасывает кэш обратной связи user.userprofile после смены роли/компании, чтобы проверки прав не «залипали»)

def can_view_all_tasks(user: User, project: Project) -> bool: ... — предикат «видит все задания проекта» (только ГИП/админ); тонкая обёртка над `_is_gip_or_admin`, используется сегмент-контролом вкладки «Задания» (Этап 3), чтобы UI и права не расходились.

**Management-команда:** `python manage.py backfill_profiles [--company ID] [--dry-run]` — создаёт `UserProfile` существующим пользователям без профиля (сигнал `create_user_profile` покрывает только новых), опционально привязывает к компании; роли не перезаписывает, идемпотентна.

**Правило:** upload_document() — единственная точка создания версий. move_document()/move_document_to_section() меняют только размещение; этап жизненного цикла меняется только через transition_document_zone(). Копирование документа СОД→СОД не поддерживается; download — выгрузка файла, не копия сущности.

## 5. Порты

### 5.1. FileStorage
class FileStorage(Protocol):
    def save(self, path: str, file) -> str: ...
    def get(self, path: str): ...
    def delete(self, path: str) -> None: ...

### 5.2. ExternalIntegrationPort
Интерфейс для общения с внешними системами (ГИП-Ассистент, ВОР-билдер, Витро-трекер).

### 5.3. LLMProvider (ai_checks/ports/llm.py, Этап 19)
class LLMProvider(Protocol):
    def complete(self, messages: list[dict], *, model: str | None = None,
                 json_mode: bool = False) -> LLMResponse: ...

Реализации:

1. **GigaChatProvider** (gigachat) — Сбер, OAuth + OpenAI-совместимый
   /chat/completions (api.giga.chat/v1). Основные модели:
   GigaChat-3-Pro (мощная), GigaChat-2-Max (быстрая), GigaChat-2 (базовая).
2. **StubLLMProvider** — заглушка без API-ключа (контур не падает,
   ответ синтезируется из найденных фрагментов).

Фабрика: ai_checks.adapters.llm_http.get_llm_provider(model).
Приоритет: GigaChat → Stub.

### 5.4. EmbeddingService (ai_checks/ports/embedding.py, Этап 19)
class EmbeddingService(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dim(self) -> int: ...            # 1024 (bge-m3 dense)
    @property
    def model_version(self) -> str: ...

Реализации (зачистка 2026-11-10): три провайдера, выбор через
фабрику ai_checks.adapters.embedding_factory.get_embedding_service():

1. **GigaChatEmbedding** (gigachat) — облако Сбера, OpenAI-совместимый
   /v1/embeddings через OAuth-токен GigaChatProvider (api.giga.chat/v1).
2. **CloudRuEmbedding** (cloudru) — Cloud.ru Foundation Models, BAAI/bge-m3,
   dense 1024, L2-нормализация (foundation-models.api.cloud.ru/v1).
3. **BgeM3OnnxEmbedding** (onnx) — локальный ONNX-инференс (CPU),
   bge-m3 dense 1024, CLS-pooling + L2 (AI_EMBEDDING_MODEL_DIR).

### 5.5. VectorBackend (ai_checks/ports/vector.py, Этап 19)
class VectorBackend(Protocol):
    def upsert(self, chunks) -> None: ...
    def search(self, vector, filters, top_k: int) -> list: ...

**Единственная реализация:** PgVectorBackend (PostgreSQL + pgvector, HNSW/cosine). Стабы NullVectorBackend и sqlite-vec не реализуются (решение 06.11.2026: Docker в локальной среде подтвержден).

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
4. Не добавлять Celery, Redis, pgvector, MCP-сервер, пока явно не попросят (ИИ-контур — Этап 19, раздел 8 — явное разрешение на Celery, Redis и pgvector; MCP-сервер в MVP не входит).
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

## 8. ИИ-контур (ai_checks) — введено в 1.6

Отдельное Django-приложение `ai_checks` (см. 2.1), изолированное от ядра storage: существующие модели (Document, DocumentVersion, Issue, Notification и др.) не изменяются — контур читает их через сервисный слой и добавляет только свои модели (раздел 3.2) и порты (разделы 5.3–5.5). Включается фича-флагом.

### 8.1. Фича-флаг

| Флаг | По умолчанию | Действие |
|---|---|---|
| `AI_CHECKS_ENABLED` | `False` | Включает URL, сервисы и Celery-задачи `ai_checks`. Миграции применяются независимо от флага (пустые таблицы не влияют на основной путь работы системы). |

### 8.2. Мультитенантность знаний

Существующая мультитенантность СОД (Project.company + UserProfile.company, раздел 3.1) дополняется уровнями видимости источников знаний:

| tenant_scope | Видимость | Обязательные поля |
|---|---|---|
| global | всем компаниям (системная база знаний) | company = NULL, project = NULL |
| company | одной компании | company (FK), project = NULL |
| project | одному проекту | project (FK); company наследуется от проекта |

Правило выборки в сервисном слое: `(tenant_scope = 'global') OR (company = текущая компания) OR (project = текущий проект)`. Изоляция — фильтрами queryset (RLS на уровне PostgreSQL в MVP не применяется). У CheckRun / PendingCheckResult / LLMCallLog собственных tenant-полей нет — company/project наследуются от document_version → document → project.

### 8.3. Инфраструктура: один кодовый путь dev = prod

- Векторное хранилище — PostgreSQL + pgvector в обоих окружениях.
- Локально: `docker-compose.dev.yml` (образы `pgvector/pgvector:pg16` и `redis:7-alpine`).
- Прод: существующий PostgreSQL, расширение подключается миграцией (`CREATE EXTENSION vector`; обратная операция — `DROP EXTENSION`).
- Стабы `NullVectorBackend` и `sqlite-vec` не реализуются (решение 06.11.2026: Docker в локальной среде подтвержден); `PgVectorBackend` — единственная реализация `VectorBackend` (5.5).
- Зависимости (`pgvector`, `onnxruntime`, `httpx` и др.) добавляются в `requirements.txt` отдельным ТЗ Кодеру на шаге Ф0.1 — документация текст требований не меняет.

### 8.4. Конвейер проверок (MVP)

1. Celery-задача берет `DocumentVersion`; PyMuPDF извлекает текст и координаты (PyMuPDF уже используется в СОД — экспорт PDF с маркапами, наложение версий).
2. Ф1: RuleEngine прогоняет активные `RuleDefinition` → `PendingCheckResult(status=pending)`.
3. Ф2: по запросу пользователя — поиск по `InternalChunk` с фильтром `tenant_scope`; ответ с цитатами и ссылками на источник.
4. Автор подтверждает/отклоняет находки (`reviewed_by`, `reviewed_at`).
5. Каждый внешний вызов модели фиксируется в `LLMCallLog`.

### 8.5. User Flow (проектный; детализируется в ТЗ фаз Ф1–Ф2)

- Запуск: карточка документа/версии → кнопка «Проверить» (видна при `AI_CHECKS_ENABLED=True`) → статус прогона отображается в карточке.
- Результат: список находок (серьезность, страница, bbox, обоснование) со ссылками на место в PDF-вьювере; у каждой находки кнопки «Подтвердить»/«Отклонить».
- RAG: поле вопроса в контексте проекта → ответ с цитатами из внутренних документов (с указанием источника и tenant-уровня).
- НЕТ UI — НЕТ ФИЧИ (HANDOFF №8): каждая фаза сдается вместе с работающим интерфейсом.

### 8.6. RAG по нормам — отложен (важно)

Готового легального API с актуальными текстами ГОСТ/СП не существует: ЦБС — платно и без публичной документации; Техэксперт — юридический риск для SaaS; данные Минстроя — реестр требований, а не библиотека текстов; Цифровой Стандарт — платно. Решение владельца (06.11.2026): RAG по нормативам для SaaS отложен; это on-premise опция (ручная загрузка 5–10 ГОСТ/СП силами организации), не часть SaaS. В MVP RAG работает по внутренним документам (требования заказчика, прошлые замечания, внутренние стандарты) — см. 8.2 и модели InternalDocument / InternalChunk.

### 8.7. VLM-проверка (Ф3) — в бэклоге

VLM-проверка запускается только после накопления эталона 30–50 размеченных листов; до этого — RuleEngine (Ф1) + RAG по внутренним документам (Ф2, урезанная). Кандидаты VLM (GLM-5.3-Flash, Qwen3-VL-Plus, DeepSeek Vision) сравниваются на эталоне; итоговый выбор — по результатам бенчмарка. Генерация отчетов для ГИПа — DeepSeek (Ф4 Этапа 19 в MASTER_SPEC.md).

## 9. Приложение help (вкладка «Помощь», этап H1)

- Модели и миграции отсутствуют: статьи — файлы docs/help/*.md (YAML-шапка: slug, title, section, order, keywords, updated).
- help/content.py — загрузчик: PyYAML для шапки, markdown-it-py (html=False) для тела, якоря заголовков через mdit-py-plugins. Кэш в процессе, сигнатура по mtime; битый файл пропускается с warning, страница не падает.
- Маршруты: /help/ (редирект на start) и /help/<slug>/ (неизвестный slug → 404). Все вьюхи под LoginRequiredMixin.
- Каталог статей задаётся settings.HELP_CONTENT_DIR (BASE_DIR / 'docs' / 'help') и попадает на прод вместе с git-чекаутом.
- Ссылка «Помощь» в топбаре статична (start); контекстность по вкладке — этап H3 (help/context_map.py).
- static/js/sw-bypass-list.js перегенерирован скриптом; /help/ исключён из кэша сервис-воркера.
