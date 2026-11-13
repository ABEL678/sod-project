# Спецификация: Модуль «Задания» (DesignTask)

**Версия:** 1.0
**Дата:** 2026-11-13
**Статус:** Черновик (на аудит)

## 1. Цель и контекст

Модуль закрывает процесс обмена заданиями между смежными разделами (АР → КР, АР → ОВ, ОВ → ЭОМ и т.д.).

В системе есть `Issue` (замечания), но нет `DesignTask` (заданий). Это разные сущности:

- **Issue** — «исправь ошибку» (NEW → IN_PROGRESS → DONE → CLOSED).
- **DesignTask** — «сделай работу» (DRAFT → ISSUED → IN_PROGRESS → ON_HOLD → COMPLETED → REVIEW → ACCEPTED / RETURNED).

**Роль ГИПа:** ГИП не выдаёт задания вручную. Он организует процесс: настраивает шаблоны типовых взаимодействий, контролирует сроки, видит все задания между отделами и вмешивается при конфликтах. Задания создаются между профильными отделами (АР → КР, ОВ → ЭОМ и т.д.) их представителями.

**Бизнес-правило:** задание всегда привязано к разделу-источнику (кто выдал) и разделу-цели (кому выдано). Входные данные — конкретные документы (листы) проекта. Выходные — файлы результата.

## 2. Модели данных (storage/domain/models.py)

### 2.1. `DesignTask`

| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| project | FK → Project, CASCADE | Проект |
| source_section | FK → Section, PROTECT | Раздел-источник (АР) |
| target_section | FK → Section, PROTECT | Раздел-цель (КР) |
| author | FK → User, PROTECT | Инициатор из отдела-источника (не обязательно ГИП) |
| assignee | FK → User, null=True | Исполнитель |
| template | FK → TaskTemplate, null=True | Шаблон |
| title | CharField(255) | Краткое название |
| description | TextField | Суть задания |
| due_date | DateField, null=True | Срок |
| status | CharField(20), choices | DRAFT, ISSUED, IN_PROGRESS, ON_HOLD, COMPLETED, REVIEW, ACCEPTED, RETURNED, CANCELLED |
| priority | CharField(10), choices | low, normal, high, critical |
| issued_at | DateTimeField, null=True | Когда выдано |
| accepted_at | DateTimeField, null=True | Когда принято |
| closed_by | FK → User, null=True | Кто закрыл |
| created_at / updated_at | DateTimeField | |

**Индекс:** `(project, source_section, target_section, title, created_at)`.

### 2.2. `DesignTaskStatusLog`

| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| task | FK → DesignTask, CASCADE, related_name='status_logs' | |
| old_status | CharField(20) | |
| new_status | CharField(20) | |
| changed_by | FK → User, PROTECT | |
| comment | TextField, blank | Причина (при возврате/уточнении) |
| created_at | DateTimeField | |

### 2.3. `DesignTaskAttachment`

| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| task | FK → DesignTask, CASCADE, related_name='attachments' | |
| document | FK → Document, null=True | Привязка к документу проекта |
| file | FileField, null=True | Свободный файл |
| kind | CharField(20), choices | input, output, comment |
| uploaded_by | FK → User, PROTECT | |
| created_at | DateTimeField | |

### 2.4. `TaskTemplate`

| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| company | FK → Company, CASCADE | Шаблон компании |
| source_section | FK → Section, null=True | Типовой источник |
| target_section | FK → Section, null=True | Типовая цель |
| title | CharField(255) | Название шаблона |
| description | TextField | Типовой текст задания |
| default_duration_days | PositiveIntegerField, default=5 | Типовой срок |
| checklist | JSONField, default=list | Список пунктов чек-листа |
| is_active | BooleanField, default=True | |

**Уникальность:** `(company, title)`.

### 2.5. `DesignTaskChecklistItem`

| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| task | FK → DesignTask, CASCADE, related_name='checklist_items' | |
| text | CharField(500) | Пункт |
| is_done | BooleanField, default=False | |
| done_by | FK → User, null=True | |
| done_at | DateTimeField, null=True | |

## 3. Сервисы (storage/domain/services.py)

```python
def create_task(
    user, project, source_section, target_section, title, description,
    due_date=None, assignee=None, template=None, priority='normal',
) -> DesignTask:
    """
    Проверка прав: user должен иметь доступ к source_section (write).
    Если template — копируем checklist в DesignTaskChecklistItem.
    Статус: DRAFT.
    """
    ...


def issue_task(user, task) -> DesignTask:
    """
    Статус DRAFT → ISSUED. Заполняет issued_at. Отправляет уведомление assignee.
    Проверка: только author или gip/admin.
    """
    ...


def update_task_status(user, task, new_status, comment='') -> DesignTask:
    """
    Матрица переходов:
      ISSUED → IN_PROGRESS (assignee)
      IN_PROGRESS → ON_HOLD (assignee, с комментарием)
      ON_HOLD → IN_PROGRESS (assignee)
      IN_PROGRESS → COMPLETED (assignee, должен приложить output)
      COMPLETED → REVIEW (author/gip)
      REVIEW → ACCEPTED (author/gip) | RETURNED (author/gip, с комментарием)
      RETURNED → IN_PROGRESS (assignee)
      Любой → CANCELLED (author/gip, с комментарием)
    Пишет DesignTaskStatusLog. Уведомляет заинтересованных.
    """
    ...


def add_task_attachment(user, task, kind, document=None, file=None) -> DesignTaskAttachment:
    """
    Права: автор/исполнитель/ГИП. Kind: input (автор), output (исполнитель), comment.
    """
    ...


def toggle_checklist_item(user, item) -> DesignTaskChecklistItem:
    """Только assignee."""
    ...


def task_summary_for_gip(project) -> dict:
    """Возвращает: total, by_status, overdue, on_hold, awaiting_review."""
    ...


def list_tasks(project, user, role_filter='all') -> QuerySet:
    """
    role_filter: 'incoming' (на меня), 'outgoing' (от меня), 'all' (gip/admin).
    """
    ...
```

### Матрица переходов статусов

| Переход | Кто | Условие |
|---|---|---|
| DRAFT → ISSUED | author / gip / admin | Заполняет `issued_at` |
| ISSUED → IN_PROGRESS | assignee | — |
| IN_PROGRESS → ON_HOLD | assignee | С комментарием |
| ON_HOLD → IN_PROGRESS | assignee | — |
| IN_PROGRESS → COMPLETED | assignee | Должен приложить output-attachment |
| COMPLETED → REVIEW | author / gip | — |
| REVIEW → ACCEPTED | author / gip | — |
| REVIEW → RETURNED | author / gip | С комментарием |
| RETURNED → IN_PROGRESS | assignee | — |
| Любой → CANCELLED | author / gip / admin | С комментарием |

## 4. UI (Шаблоны и вьюхи)

### 4.1. Вкладка «Задания» на странице проекта (`/projects/<id>/?tab=tasks`)

- Сверху — сегмент-контрол: **Все / Входящие / Исходящие** (для ГИПа — все; для остальных — только incoming/outgoing).
- Таблица: Заголовок | Источник → Цель | Автор | Исполнитель | Срок | Статус | Действия.
- Кнопка «Выдать задание» (видна, если у пользователя есть write-доступ хотя бы к одному разделу).
- Фильтры: статус, срок (просроченные / на этой неделе), приоритет.

### 4.2. Форма создания задания (`/projects/<id>/tasks/new/`)

- Поля: `source_section`, `target_section`, `title`, `description`, `due_date`, `assignee`, `template` (выпадающий, если есть шаблоны), `priority`.
- Если выбран `template` — автозаполнение title/description/срок.
- Кнопки: «Сохранить черновик» / «Выдать» (ISSUED).

### 4.3. Карточка задания (`/projects/<id>/tasks/<task_id>/`)

- **Шапка:** заголовок, статус-бейдж, приоритет, срок (с живым дедлайном — «осталось 3 дня»).
- **Блок «Суть»:** `description`.
- **Блок «Входные данные»:** список attachments (`kind=input`) + привязка к документам.
- **Блок «Результат»:** attachments (`kind=output`).
- **Блок «Чек-лист»:** список пунктов с чекбоксами (только для assignee).
- **Блок «История»:** таймлайн `status_logs` + комментарии.
- **Панель действий** (по правам и статусу):
  - **Assignee:** «Принять в работу», «Приостановить», «Выполнено» (с загрузкой output).
  - **Author/GIP:** «Вернуть на доработку» (с комментарием), «Принять», «Отменить».

### 4.4. Дашборд ГИПа (`/projects/<id>/tasks/dashboard/`)

- Сводка: всего, в работе, на проверке, просрочено, на уточнении.
- Таблица «Требуют внимания»: просроченные, ожидающие проверки, на уточнении.
- Экспорт в Excel (открытые задания).

## 5. Уведомления

Использовать существующую модель `Notification` (`storage.domain.models.Notification`) + Celery-задачи для отложенных напоминаний:

- **При выдаче:** assignee — «Вам выдано задание #N».
- **При смене статуса:** заинтересованные — «Задание #N: \<статус\>».
- **Напоминания:**
  - За 2 дня до `due_date` — assignee.
  - В день `due_date` — assignee + author.
  - При просрочке (`due_date + 1 день`) — assignee + author + gip.
- **Задачи Celery:** `notify_task_due_soon`, `notify_task_due_today`, `notify_task_overdue` (`periodic_task`, `celery-beat`).

## 6. Шаблоны и чек-листы

- В админке (или отдельной вкладке «Настройки → Шаблоны заданий») — CRUD `TaskTemplate`.
- Чек-лист — JSON-массив строк: `["Проверить нагрузки", "Сделать расчёт", "Согласовать с ГИПом"]`.
- При создании задания по шаблону — `checklist` копируется в `DesignTaskChecklistItem`.

## 7. Интеграции

- **Документы:** в карточке документа (`document_detail.html`) — блок «Связанные задания» (по `DesignTaskAttachment.document`).
- **RAG:** в `rag_answer` учитывать задания (если вопрос про задания — искать в `DesignTask.description/title`). Опционально: отдельный источник в RAG.
- **ГИП-Ассистент:** модуль DesignTask может синхронизироваться с Task/Decision из ГИП-Ассистента через `external_id` (поле в модели). API-контракт: POST/GET `/api/v1/projects/{code}/tasks/`. Это позволит ГИПу выдавать задания из своей среды, а проектировщикам — выполнять их в Stream-CAD.
- **API Gateway:** эндпоинт `GET /api/v1/projects/{code}/tasks/` (опционально, позже).

## 8. Правила и ограничения

- Бизнес-логика — только в `services.py`.
- Модели — новые, существующие (`Document`, `Issue`) не меняются.
- Миграции — новые (`design_task`, `status_log`, `attachment`, `template`, `checklist_item`).
- UI — обязателен (правило «НЕТ UI — НЕТ ФИЧИ»).
- Уведомления — через существующий `Notification`, без новых моделей.
- Все действия — с проверкой прав (author/assignee/gip/admin).

### Этап 1. Модели и миграции

- Модели `DesignTask`, `DesignTaskStatusLog`, `DesignTaskAttachment`, `TaskTemplate`, `DesignTaskChecklistItem`.
- Миграция в `storage`.
- Регистрация в админке (базовая).
- `manage.py check` — no issues.

### Этап 2. Сервисы (бизнес-логика)

- `create_task`, `issue_task`, `update_task_status`, `add_task_attachment`, `toggle_checklist_item`, `task_summary_for_gip`, `list_tasks`.
- Матрица переходов статусов (в сервисе).
- Проверки прав.
- Точечные тесты (unit) для сервисов.

### Этап 3. UI: список и форма создания

- Вкладка «Задания» на странице проекта (сегмент-контрол, таблица, кнопка «Выдать задание»).
- Форма создания (с шаблоном).
- Вьюхи + шаблоны.

### Этап 4. UI: карточка задания

- Карточка с блоками (суть, входные данные, результат, чек-лист, история).
- Панель действий (по правам и статусу).
- Привязка к документам (attachments).

### Этап 5. Уведомления и напоминания

- Сигналы/сервисы при смене статуса.
- Celery-задачи: `notify_task_due_soon`, `notify_task_due_today`, `notify_task_overdue` (periodic, celery-beat).
- Тесты.

### Этап 6. Дашборд ГИПа

- Сводка, таблица «Требуют внимания», экспорт в Excel.

### Этап 7. Шаблоны и чек-листы (CRUD)

- UI для управления `TaskTemplate` (в админке или отдельной вкладке).
- Копирование `checklist` в задание.
- Тесты.

### Этап 8. Интеграция с документами и RAG

- Блок «Связанные задания» в `document_detail.html`.
- Опционально: учёт заданий в `rag_answer` (источник).
- Тесты.

## ПРОМПТЫ ДЛЯ АУДИТОРА

Формат: по каждому этапу — промпт, который Аудитор получает для формирования `.patch`. Патчи применяются Кодером через `git apply`. Если патч не ложится — Аудитор уточняет по фактическому коду (`git pull`).

### Промпт Аудитору для Этапа 1 (модели и миграции)

**Контект:** репозиторий `github.com/ABEL678/sod-project`, ветка `main`, HEAD `ffd5303` (или актуальный). Стек: Django 6.1 + Bootstrap 5 + HTMX + ванильный JS. Архитектура: «гексагон лайт» — бизнес-логика в `storage/domain/services.py`, модели в `storage/domain/models.py`.

**Задача:** создать 5 моделей для модуля «Задания» (спецификация выше): `DesignTask`, `DesignTaskStatusLog`, `DesignTaskAttachment`, `TaskTemplate`, `DesignTaskChecklistItem`.

**Требования:**

- Все модели — в `storage/domain/models.py` (рядом с `Issue`, `Document`).
- `DesignTask.status` — choices: DRAFT, ISSUED, IN_PROGRESS, ON_HOLD, COMPLETED, REVIEW, ACCEPTED, RETURNED, CANCELLED.
- `DesignTask.priority` — choices: low, normal, high, critical.
- `DesignTaskAttachment.kind` — choices: input, output, comment.
- `TaskTemplate.checklist` — JSONField (`default=list`).
- `DesignTaskChecklistItem` — без уникальности (одно и то же задание может иметь несколько одинаковых пунктов).
- Миграции — новые (в `storage/migrations/`). Не менять существующие модели (`Document`, `Issue` и др.).
- `manage.py check` — no issues.
- Регистрация в `storage/admin.py` (базовая: `list_display`, `search_fields`).
- Не коммитить — сформировать `.patch` (`git format-patch`) в `docs/audit/design-task-stage1/`.

**Выход:** `.patch` + отчёт с перечнем изменённых файлов и выводом `manage.py check`.

### Промпт Аудитору для Этапа 2 (сервисы)

**Контекст:** после применения Этапа 1 (модели `DesignTask` и др. существуют). Бизнес-логика — только в `storage/domain/services.py`.

**Задача:** реализовать сервисы:

1. `create_task(user, project, source_section, target_section, title, description, due_date=None, assignee=None, template=None, priority='normal') -> DesignTask`
2. `issue_task(user, task) -> DesignTask`
3. `update_task_status(user, task, new_status, comment='') -> DesignTask`
4. `add_task_attachment(user, task, kind, document=None, file=None) -> DesignTaskAttachment`
5. `toggle_checklist_item(user, item) -> DesignTaskChecklistItem`
6. `task_summary_for_gip(project) -> dict`
7. `list_tasks(project, user, role_filter='all') -> QuerySet`

**Требования:**

- Матрица переходов статусов — в `update_task_status` (по правилам из спецификации). При недопустимом переходе — `PermissionDenied` или `ValidationError`.
- Проверки прав:
  - `create_task`: user должен иметь write-доступ к `source_section` (использовать существующий `check_write_permission`).
  - `issue_task`: только author или gip/admin.
  - `update_task_status`: по матрице (assignee / author / gip / admin).
  - `add_task_attachment`: по kind (input — author, output — assignee, comment — все участники).
- При `issue_task` и `update_task_status` — запись в `DesignTaskStatusLog`.
- При создании по шаблону (`template`) — копировать `checklist` в `DesignTaskChecklistItem`.
- При `COMPLETED → REVIEW` — требовать наличие хотя бы одного output-attachment (иначе `ValidationError`).
- Уведомления — через существующий `Notification` (создавать запись с `recipient`, `message`, `url`). Не создавать новые модели уведомлений.
- Точечные тесты (unit) для сервисов — в `storage/tests/test_design_tasks.py` (минимум: создание, выдача, переходы, права).
- Не коммитить — `.patch` в `docs/audit/design-task-stage2/`.

**Выход:** `.patch` + отчёт (список тестов, `manage.py test storage.tests.test_design_tasks`).

### Промпт Аудитору для Этапа 3 (UI: список и форма создания)

**Контекст:** после Этапа 2 (сервисы работают). UI — Bootstrap 5 + HTMX, шаблоны в `storage/adapters/templates/storage/`. Вкладки проекта — в `_project_tabs.html` (или аналог).

**Задача:**

- Добавить вкладку «Задания» на странице проекта (`/projects/<id>/?tab=tasks`).
- Реализовать список заданий: сегмент-контрол (Все / Входящие / Исходящие), таблица (Заголовок, Источник → Цель, Автор, Исполнитель, Срок, Статус, Действия), фильтры (статус, срок, приоритет).
- Кнопка «Выдать задание» (видна, если есть write-доступ к любому разделу).
- Форма создания (`/projects/<id>/tasks/new/`): поля (`source_section`, `target_section`, `title`, `description`, `due_date`, `assignee`, `template`, `priority`). При выборе `template` — автозаполнение.
- Вьюхи: `TaskListView` (или функция), `TaskCreateView` (HTMX-фрагмент или полноценная страница).
- Проверка прав: `create_task` через сервис.
- Не коммитить — `.patch` в `docs/audit/design-task-stage3/`.

**Выход:** `.patch` + отчёт (список шаблонов, скриншоты или описание UI).

### Промпт Аудитору для Этапа 4 (UI: карточка задания)

**Контекст:** после Этапа 3 (список и форма создания работают).

**Задача:**

- Карточка задания (`/projects/<id>/tasks/<task_id>/`): шапка (заголовок, статус-бейдж, приоритет, живой дедлайн), блоки (Суть, Входные данные, Результат, Чек-лист, История).
- Панель действий (по правам и статусу):
  - **Assignee:** «Принять в работу», «Приостановить», «Выполнено» (с загрузкой output).
  - **Author/GIP:** «Вернуть на доработку» (с комментарием), «Принять», «Отменить».
- История: таймлайн `status_logs` + комментарии.
- Чек-лист: чекбоксы (только assignee), AJAX-обновление (HTMX).
- Вложения: загрузка input/output, привязка к документам.
- Не коммитить — `.patch` в `docs/audit/design-task-stage4/`.

**Выход:** `.patch` + отчёт (скриншоты карточки, список шаблонов).

### Промпт Аудитору для Этапа 5 (уведомления и напоминания)

**Контекст:** после Этапа 4 (карточка работает). Есть Celery + Redis (настроены на проде). Есть модель `Notification`.

**Задача:**

- При смене статуса (через `update_task_status`) — создавать `Notification` для заинтересованных:
  - `issue_task`: assignee — «Вам выдано задание #N».
  - `COMPLETED → REVIEW`: author/gip — «Задание #N ожидает проверки».
  - `REVIEW → RETURNED`: assignee — «Задание #N возвращено на доработку. Причина: ...».
  - `REVIEW → ACCEPTED`: assignee — «Задание #N принято».
- Celery-задачи (periodic, celery-beat):
  - `notify_task_due_soon` — за 2 дня до `due_date`.
  - `notify_task_due_today` — в день `due_date`.
  - `notify_task_overdue` — на следующий день после `due_date`.
- Настройка celery-beat (расписание) в `sod_core/celery.py` или `settings.py`.
- Тесты: unit для уведомлений (проверка создания `Notification`), интеграционные для Celery-задач (мок).
- Не коммитить — `.patch` в `docs/audit/design-task-stage5/`.

**Выход:** `.patch` + отчёт (список задач, расписание beat, тесты).

### Промпт Аудитору для Этапа 6 (дашборд ГИПа)

**Контекст:** после Этапа 5 (уведомления работают).

**Задача:**

- Дашборд ГИПа (`/projects/<id>/tasks/dashboard/`): сводка (всего, в работе, на проверке, просрочено, на уточнении), таблица «Требуют внимания» (просроченные, ожидающие проверки, на уточнении).
- Экспорт в Excel (открытые задания) — через `openpyxl` или существующий механизм экспорта.
- Доступ: только gip/admin проекта.
- Не коммитить — `.patch` в `docs/audit/design-task-stage6/`.

**Выход:** `.patch` + отчёт (скриншоты дашборда, тесты).

### Промпт Аудитору для Этапа 7 (шаблоны и чек-листы, CRUD)

**Контекст:** после Этапа 6 (дашборд работает).

**Задача:**

- CRUD для `TaskTemplate`: в админке или отдельной вкладке «Настройки → Шаблоны заданий».
- Поля: `company`, `source_section`, `target_section`, `title`, `description`, `default_duration_days`, `checklist` (JSON-массив строк).
- При создании задания по шаблону — `checklist` копируется в `DesignTaskChecklistItem`.
- Тесты: CRUD шаблонов, копирование `checklist`.
- Не коммитить — `.patch` в `docs/audit/design-task-stage7/`.

**Выход:** `.patch` + отчёт (скриншоты CRUD, тесты).

### Промпт Аудитору для Этапа 8 (интеграция с документами и RAG)

**Контекст:** после Этапа 7 (шаблоны работают).

**Задача:**

- В `document_detail.html` — блок «Связанные задания» (по `DesignTaskAttachment.document`).
- В `rag_answer` (или новом сервисе) — учитывать задания: если вопрос содержит «задание», «смежник», «выдано» — искать в `DesignTask.title/description` и возвращать как источник (опционально, по согласованию с Координатором).
- Тесты: unit для RAG с заданиями.
- Не коммитить — `.patch` в `docs/audit/design-task-stage8/`.

**Выход:** `.patch` + отчёт (скриншоты, тесты).
