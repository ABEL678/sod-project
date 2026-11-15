# План: Модуль «Запросы информации (RFI)» и интеграция с ГИП-Ассистентом

**Проект:** Stream-CAD (СОД), Django 6.1.
**Автор:** Аудитор (Z.ai / GLM-5.3-Flash, режим Debug).
**Дата:** 2026-11-16.
**База:** `main`, HEAD = `f0514da` (Этап 6 закрыт: «Сводка по заданиям смежников», Excel-экспорт, UI-API).
**Статус:** план для утверждения Координатором. Код не пишется — только проектирование.
**Внешняя система:** «ГИП-Ассистент» (GitHub: ABEL678/gip-assistant, HEAD = `366ab96`, FastAPI + гексагон).

---

## 1. Цель и контекст

RFI (Request For Information) — запрос внешних исходных данных (ИД) у Заказчика,
**блокирующий работу** проекта. В СОД сегодня такого процесса нет: «Задания»
(DesignTask) покрывают обмен между смежными разделами внутри команды, но не
запросы наружу (ТУ, ГПЗУ, изыскания, техусловия у заказчика).

Модуль закрывает две задачи:

1. **Внутренняя (СОД):** отдельная сущность `RfiTask` со своим жизненным циклом,
   файлы запросов и ответов Заказчика в папке
   «Исходные данные (ИРД) → Запросы информации (RFI)», вкладка на странице
   проекта, блок на «Сводке».
2. **Внешняя (интеграция):** токен-эндпоинты API, по которым ГИП-Ассистент
   забирает **полный список заданий** (для своей матрицы смежников) и **список
   RFI** (особенно в статусе `escalated`) — в форматах, максимально совместимых
   с их моделями `Task` и RFI (Task + `rfi_state`).

## 2. Результаты изучения репозитория ГИП-Ассистента

Изучено: `backend/src/domain/entities/task.py`, `document.py`, `project.py`,
`backend/src/domain/value_objects/enums.py` (`RfiState`, `TaskStatus`,
`CrossTaskStatus`), `backend/src/application/dto/task.py`, `rfi.py`,
`cross_task.py`, `project.py`, `backend/src/domain/services/rfi_service.py`,
`backend/src/adapters/api/routes/rfis.py`, `matrix.py`, `docs/specs/MASTER_SPEC.md`,
`docs/context/DOMAIN.md` (§2.3, §4.6), `.env.example`.

### 2.1. Как у них устроен RFI (DOMAIN.md §4.6, rfi_service.py)

- RFI — **особая разновидность Task**: `Task.is_rfi = true`, у обычной задачи
  есть поля `rfi_cause_decision_id` / `rfi_cause_goal_id` (XOR — ровно одна
  причина), `rfi_due_date` (срок ожидания ответа) и `rfi_state`.
- Причина RFI — **всегда нехватка ИД** для реализации Decision или Goal.
- Жизненный цикл через Actions: «составить запрос → отправить → получить ответ →
  зарегистрировать новые ИД». Результат — Document с новыми ИД, который
  пополняет вводные (Premise с `source_document_id`).
- `RfiState` (enums.py:156): `open`, `sent`, `answered`, `registered`, `closed`,
  `escalated` — **значения строкой совпадают 1:1** с набором статусов, который
  задал Координатор для `RfiTask`. Семантика: ANSWERED = «ответ получен
  (Document с rfi_id)», REGISTERED = «новые ИД зарегистрированы».
- RFI создают вручную (ГИП) или предлагает система (`RfiProposal`, правило
  «decision без ИД больше N дней»); их агент RFI (6.12e) следит за сроками и
  эскалирует (`POST /api/rfis/{id}/escalate`).
- У их `Document` есть поле `rfi_id` — ответ RFI это документ (1:N).

### 2.2. Что им нужно для матрицы связей (matrix.py, cross_task.py, task.py)

- Матрица смежников строится из `Task.section_code` (**от кого**, источник) и
  `Task.to_section_code` (**кому**, получатель) — точные аналоги наших
  `DesignTask.source_section.code` / `target_section.code`.
- Ячейка матрицы — `CrossTask` со статусами `NOT_STARTED / IN_PROGRESS /
  WAITING_INFO / DONE / OVERDUE` (`WAITING_INFO` = «ждёт информации» — это и
  есть слот, куда ложится RFI/блокировка).
- Поле `TaskStatus` у них: `NOT_STARTED, IN_PROGRESS, REVIEW, REWORK, DONE,
  CANCELLED`. Полей `priority` в `Task` нет (есть `is_complex: bool`,
  `sort_order`, `source: str`).
- В `CrossTaskTaskRead` есть вычисляемые `overdue_days` и
  `days_until_deadline` — полезно отдавать сразу в нашем API.
- Ссылка на проект — **по `code`** (`Project.code`, ≤50 симв.): наш
  `Project.code` совместим; ГА может создавать проекты у нас через уже
  работающий `POST /api/v1/projects/` (scope `projects:create`).

### 2.3. Аутентификация и режим интеграции

- Auth (JWT + Argon2) у них — веха 6 («потом»), сейчас агенты работают
  «read-only через существующий API». В `.env.example` уже есть слот
  `JWT_SECRET`, но ни исходящих токенов, ни хранения секретов соседних систем
  ещё нет.
- Вывод: для них оптимален **статичный Bearer-токен** в `.env`
  (`STREAM_CAD_API_TOKEN=...`) — ровно то, что СОД уже даёт через
  `external_api` (модель `ApiToken`: SHA-256-хэш, префикс, scopes, срок,
  отзыв; аутентификация `Authorization: Bearer <token>`; permission
  `HasScope`; изоляция компаний через `token.company`).
- Опрос (polling) — их модель работы (агенты читают API по расписанию);
  `UserRateThrottle` у нас уже включён на API-вьюхах.

## 3. Терминология (жёсткое требование №3)

Слово «Дашборд» в пользовательских текстах **не используется**:

| Где | Как называется |
|---|---|
| Страница Этапа 6 | «Сводка по заданиям смежников» (уже переименовано в f0514da) |
| Блок RFI на странице Этапа 6 | «Запросы информации (RFI)» |
| Новая вкладка проекта | «Запросы информации (RFI)» (короткое имя вкладки — «RFI») |
| Кнопки/формы/письма | «Создать запрос информации», «Отправить», «Эскалировать» и т.п. |

Технические идентификаторы, не видимые в интерфейсе (имя вьюхи
`TaskDashboardView`, URL `/tasks/dashboard/`, имя маршрута `task_dashboard`),
в MVP не переименовываются — это осознанное решение (риск рефакторинга без
пользы для пользователя; URL виден только в адресной строке). Выносится в
бэклог отдельным мелким патчем, если Координатор решит иначе.

## 4. Решения проектирования (D1–D8)

| № | Решение | Обоснование | Альтернатива (отклонена) |
|---|---|---|---|
| **D1** | RFI — **отдельная модель** `RfiTask` (+ `RfiStatusLog`) в `storage/domain/models.py`. Никаких флагов `is_rfi` на DesignTask | Жёсткое требование Координатора №1; совпадает с тем, что в СОД «Задание» и «Запрос ИД» — разные процессы с разными статусами | Флаг на DesignTask — запрещено |
| **D2** | Статусы `RfiTask` — строковые значения `open, sent, answered, registered, closed, escalated` | **Побайтово совпадают** с `RfiState` ГИП-Ассистента → JSON-совместимость без маппинга | UPPER_CASE как у DesignTask — потребовал бы транслитерацию в API |
| **D3** | К минимальному набору полей Координатора добавляются: `subject` (краткое название), `number` (порядковый, «RFI-07»), `external_id` (UUID RFI у ГА), таймстампы `sent_at/answered_at/registered_at/escalated_at/closed_at`, `closed_by` | Без `subject`/`number` таблицы и API нечитаемы (`reason` — длинный текст); `external_id` — симметрично `DesignTask.external_id` (спека 7) | Жёсткий набор из 8 полей — не хватает для UI и API |
| **D4** | Матрица переходов: 9 разрешённых переходов (см. раздел 6). `close` из `open` = отмена запроса (комментарий обязателен); терминальный статус — `closed` | У Координатора 6 статусов без «cancelled»: отмена обязана вести в терминальное состояние; happy path — `open → sent → answered → registered → closed` | Отдельный статус `cancelled` — расширяет набор, ломает 1:1 с RfiState |
| **D5** | Права: **создание** RFI — gip/admin проекта + developer/contractor (участники); **переходы и файлы** — author, assignee, gip, admin; **чтение** — все участники проекта. `assignee` по умолчанию = `project.gip` | Запросить ИД может и разработчик (он знает, чего не хватает), но вести запрос и отвечать за связь с Заказчиком — ГИП. Симметрично `check_write_permission` и `can_view_all_tasks` | Только ГИП создаёт — теряется инициатива с мест; все создают — viewer/customer/checker ошибочно получают право |
| **D6** | Файлы RFI — **настоящие Document** в папке «Исходные данные (ИРД) → Запросы информации (RFI)» (раздел 8), привязка через FK `document_request` / `document_response` | Требование Координатора №2 + семантика ГА («ответ = Document»); документ попадает в файловую структуру, поиск, версии, RAG | FileField-вложения как у DesignTaskAttachment — выпадают из структуры ИРД |
| **D7** | Для загрузки файла в папку RFI пользователь с правом RFI, но без write-права в COMMON (например, developer-автор), идёт через **`upload_document(..., skip_section_write_check=False)`** — новый обратно совместимый kwarg; сервис RFI сам проверяет права по матрице D5 перед вызовом | `upload_document` — единственная точка создания версий: валидация расширений/размера, ГОСТ-шифры, версионирование, AI-индексация. Дублировать её в RFI-сервисе — риск расхождения. COMMON-раздел выдаёт write только ГИП/админу, а автор RFI может быть developer | (a) Прямой ORM-код в RFI-сервисе — дублирует 100+ строк валидации; (b) писать от имени ГИПа — искажает `uploaded_by`/аудит |
| **D8** | API для ГА — **новые эндпоинты в `external_api`** (`/api/v1/projects/<code>/tasks/`, `/api/v1/projects/<code>/rfis/`) с новым scope `tasks:read`. Существующий UI-эндпоинт `/projects/<id>/tasks/dashboard/api/` остаётся сессионным (JS интерфейса), не переиспользуется для внешних систем | UI-эндпоинт сидит на `LoginRequiredMixin` и `get_accessible_projects(user)` — несовместим с Bearer-токеном; паттерн external_api (изоляция по `token.company`, scope, конверт ошибок `{"errors": …}`) уже обкатан на 5 эндпоинтах Этапа 4A–4D и описан в ARCHITECTURE.md §6.2 | Давать токенам доступ к UI-пространству storage — грязное смешение аутентификаций и риск по правам |

## 5. Модель данных

### 5.1. `RfiTask` (storage/domain/models.py)

Назначение: запрос внешних ИД у Заказчика. Отдельная сущность (D1), не флаг на
DesignTask. Место в файле — после `DesignTaskChecklistItem`, с комментарием-
шапкой по образцу блока DesignTask (ссылка на этот план).

| Поле | Тип | Смысл |
|---|---|---|
| `project` | FK → Project, CASCADE, `related_name='rfi_tasks'` | Проект |
| `number` | PositiveIntegerField | Порядковый номер в проекте: display «RFI-{number}» (ноль-паддинг до 2 цифр в UI-хелпере, в БД — целое) |
| `subject` | CharField(255) | Краткое название запроса («ТУ на водоснабжение») |
| `reason` | TextField | Суть: каких данных не хватает, почему, что блокирует (семантика ГА: «всегда нехватка данных») |
| `author` | FK → User, PROTECT, `related_name='created_rfi_tasks'` | Инициатор |
| `assignee` | FK → User, SET_NULL, null/blank, `related_name='assigned_rfi_tasks'` | Ответственный за получение ответа (по умолчанию — ГИП проекта, сервис `create_rfi`) |
| `status` | CharField(20), choices, default=`open`, db_index | `open / sent / answered / registered / closed / escalated` (D2) |
| `rfi_due_date` | DateField, null/blank | До какого числа ждём ответ Заказчика (для эскалации и просрочки) |
| `document_request` | FK → Document, SET_NULL, null/blank, `related_name='rfi_as_request'` | Файл запроса (в папке ИРД → RFI) |
| `document_response` | FK → Document, SET_NULL, null/blank, `related_name='rfi_as_response'` | Файл ответа Заказчика (новые ИД) |
| `external_id` | CharField(100), null/blank, db_index | UUID RFI в ГИП-Ассистенте (фаза 2; симметрично `DesignTask.external_id`) |
| `sent_at`, `answered_at`, `registered_at`, `escalated_at`, `closed_at` | DateTimeField, null/blank | Таймстампы переходов (заполняют сервисы) |
| `closed_by` | FK → User, SET_NULL, null/blank, `related_name='closed_rfi_tasks'` | Кто закрыл |
| `created_at`, `updated_at` | auto_now_add / auto_now | — |

- `unique_together = ('project', 'number')` — номер уникален в проекте.
- Индексы: `(project, status)` — вкладка/фильтры; `(project, rfi_due_date)` —
  выборка просроченных и beat-эскалация.
- `Meta.verbose_name` = «Запрос информации (RFI)» / «Запросы информации (RFI)».
- SET_NULL у `document_request/response` — как у `DesignTaskAttachment.document`:
  мягкое удаление документа не должно стирать историю RFI.

### 5.2. `RfiStatusLog` (история)

По образцу `DesignTaskStatusLog`: `rfi` (FK, CASCADE, `related_name='status_logs'`),
`old_status`, `new_status` (CharField(20)), `changed_by` (FK → User, PROTECT),
`comment` (TextField, blank), `created_at`. Пишется каждым сервисом перехода.

### 5.3. Миграция

`storage/migrations/0033_rfi_task_stage1.py` — новые модели, существующие не
трогаются. Ожидаемый дрейф `makemigrations --check` — 2 предсуществующих
label-only AlterField (`Document.suitability_code`, `Folder.zone`), новых
миграций кроме 0033 не появляется.

## 6. Матрица переходов статусов и права

### 6.1. Переходы (D4)

| № | Из | В | Сервис | Кто | Условия |
|---|---|---|---|---|---|
| 1 | `open` | `sent` | `send_rfi` | author, assignee, gip, admin | обязателен `document_request` (файл запроса сформирован и загружен) |
| 2 | `sent` | `answered` | `answer_rfi` | author, assignee, gip, admin | фиксирует факт получения ответа (файл может быть приложен этим же шагом или позже) |
| 3 | `escalated` | `answered` | `answer_rfi` | author, assignee, gip, admin | ответ пришёл после эскалации — возврат в штатный цикл |
| 4 | `answered` | `registered` | `register_rfi_response` | author, assignee, gip, admin | обязателен `document_response` — документ, загруженный в папку ИРД → RFI |
| 5 | `sent` | `escalated` | `escalate_rfi` | assignee, gip, admin | вручную (истёк срок, нет реакции); также автоматически beat-задачей (раздел 11) |
| 6 | `answered` | `escalated` | `escalate_rfi` | assignee, gip, admin | ответ получен, но неполный/не открывает блокировку |
| 7 | `registered` | `closed` | `close_rfi` | author, assignee, gip, admin | happy-path закрытие: ИД получены и зарегистрированы |
| 8 | `escalated` | `closed` | `close_rfi` | gip, admin | закрытие без ответа (решение ГИПа: «запрос неактуален») |
| 9 | `open` | `closed` | `close_rfi` | author, gip, admin | отмена черновика запроса; **комментарий обязателен** |

Терминальный статус — `closed` (из него переходов нет). Все прочие переходы —
запрещены матрицей (сервис возвращает `PermissionDenied`/`ValueError`, как
`update_task_status`). Каждый переход пишет `RfiStatusLog` + таймстамп.

«Кто» — грейд-лист: `author` — создатель RFI; `assignee` — ответственный;
`gip` — `Project.gip` или `ProjectMembership.role_in_project='gip'`; `admin` —
`UserProfile.role='admin'` компании проекта или superuser (тот же предикат,
что `_is_gip_or_admin`, см. services.py:4887–4912).

### 6.2. Права на чтение и создание

- **Чтение** RFI проекта — все участники проекта (`_ensure_project_access`).
- **Создание** — роли `gip`, `admin`, `developer`, `contractor` среди
  участников проекта (D5).
- Вкладка «RFI» видна всем участникам (как вкладка «Задания», фикс 3c95380);
  кнопки действий рендерятся по матрице 6.1.

## 7. Сервисы (storage/domain/services.py)

Бизнес-логика — только в сервисном слое («гексагон лайт»). Вьюхи — тонкие.
Дополнение к минимальному списку Координатора (`create, send, answer, close`)
— `register_rfi_response` и `escalate_rfi`: без них статусы `registered` и
`escalated` недостижимы (D4), т.е. это покрытие матрицы, не «лишнее».

| Сервис | Сигнатура (уровень дизайна) | Поведение |
|---|---|---|
| `create_rfi` | `(user, project, subject, reason, assignee=None, rfi_due_date=None) -> RfiTask` | Права D5; `number = max+1` в рамках проекта (`unique_together` защищает гонку; при `IntegrityError` — повтор); `assignee` по умолчанию — `project.gip`; статус `open` |
| `send_rfi` | `(user, rfi, comment='') -> RfiTask` | Переход 1; проверяет `document_request is not None`; ставит `sent_at` |
| `answer_rfi` | `(user, rfi, comment='') -> RfiTask` | Переходы 2/3; ставит `answered_at` |
| `register_rfi_response` | `(user, rfi, document) -> RfiTask` | Переход 4; документ — проекта RFI, в папке RFI (или её потомке); ставит `document_response`, `registered_at` |
| `escalate_rfi` | `(user, rfi, comment='') -> RfiTask` | Переходы 5/6; ставит `escalated_at` |
| `close_rfi` | `(user, rfi, comment='') -> RfiTask` | Переходы 7/8/9; для перехода 9 комментарий обязателен; ставит `closed_at`, `closed_by` |
| `upload_rfi_document` | `(user, rfi, kind, file, name='') -> Document` | `kind` ∈ {`request`, `response`}; права по D5 (`request` — author/assignee/gip/admin; `response` — gip/admin/assignee); создаёт папку при необходимости; вызывает `upload_document` с kwarg-ом D7; шифр `RFI-{number}-Запрос` / `RFI-{number}-Ответ`, doc_type `source_data`; для `kind=response` сразу привязывает `document_response` |
| `attach_rfi_document` | `(user, rfi, kind, document) -> RfiTask` | Привязка уже загруженного в папку RFI документа (выбор из файлов папки в карточке); те же проверки принадлежности, что в `register_rfi_response` |
| `get_or_create_rfi_folder` | `(project, actor) -> Folder` | Идемпотентно создаёт «Запросы информации (RFI)» под корневой «Исходные данные (ИРД)» (раздел 8) |
| `list_rfis` | `(project, user, status_filter=None) -> QuerySet` | `status_filter` — список статусов или None (все); select_related `project/author/assignee/document_request/document_response`; доступ — участник проекта |
| `rfi_summary_for_gip` | `(project) -> dict` | `{total, open, sent, answered, registered, escalated, closed, overdue}`; `overdue` = `sent`/`escalated` с `rfi_due_date < today` (открытый `open` не просрочен — ещё не отправлен) |
| `rfis_requiring_attention` | `(project) -> list[RfiTask]` | Для «Сводки»: `sent` просроченные + `escalated`; аннотирует `is_overdue`, `overdue_days`, `attention_reason` (`overdue_sent` / `escalated`); сортировка: escalated → overdue, затем `rfi_due_date` |
| `escalate_overdue_rfis` | `() -> int` | Для beat (раздел 11): `sent` + `rfi_due_date < today` → `escalated` (`comment='Автоэскалация: истёк срок ответа Заказчика'`, `changed_by` = `assignee or project.gip`); возвращает число |

Ошибки — по образцу модуля «Задания»: `PermissionDenied` (права), `ValueError`
(смысловые нарушения: нет файла запроса, документ не из папки RFI, чужой
проект), `ValidationError` (формы). Уведомления — через `_notify_rfi`
(раздел 11). Флаг `_from_service` не нужен — у `RfiTask.save()` нет
FSM-перехватчика, матрицу держат только сервисы.

## 8. Папка «Исходные данные (ИРД) → Запросы информации (RFI)» и документы

### 8.1. Структура

```
Исходные данные (ИРД)               ← корневая папка (ROOT_SUPERFOLDERS, key='IRD')
├── Инженерные изыскания
├── ГПЗУ (Градостроительный план земельного участка)
├── ТУ (Технические условия)
├── ЗнП (Задание на проектирование)
├── СТУ (Специальные технические условия)
├── Прочие исходные данные
└── Запросы информации (RFI)        ← НОВАЯ подпапка (section=COMMON, zone=WIP)
    ├── RFI-1-Запрос.pdf
    ├── RFI-1-Ответ.pdf
    └── ...
```

- Папка привязана к скрытому разделу `COMMON` — как все ИРД-подпапки
  (`_create_ird_structure`, services.py:795–823). После `sync_project_sections`
  она не перепривязывается: синхронизация двигает только папки, совпадающие с
  марками шаблона, «Запросы информации (RFI)» — не марка.
- Два пути создания:
  1. **Новые проекты:** элемент `{'name': 'Запросы информации (RFI)'}` в конец
     `IRD_FOLDERS` (project_templates.py:43) — создастся автоматически вместе с
     остальной ИРД-структурой.
  2. **Существующие проекты:** ленивое `get_or_create_rfi_folder(project,
     actor)` — вызывается сервисами `create_rfi` / `upload_rfi_document`
     перед первой загрузкой. `reset_project_structure` тоже покроет её (он
     идёт по `IRD_FOLDERS`).
- Удалять/переименовывать папку RFI нельзя (см. риски, раздел 15).

### 8.2. Документы запроса и ответа

- Запрос и ответ — полноценные `Document` + `DocumentVersion` в проекте:
  попадают в таблицу документов, поиск, версионирование, RAG-индексацию —
  и остаются в системе после закрытия RFI (как архив ИД).
- Шифры: `RFI-{number}-Запрос`, `RFI-{number}-Ответ` (уникальны в рамках
  `(project, COMMON)` благодаря уникальности `number`). Имя документа:
  `RFI-{number}: Запрос — {subject}` / `RFI-{number}: Ответ Заказчика`.
  `doc_type='source_data'`. При загрузке ответа новыми версиями (Заказчик
  прислал уточнение) — `DocumentVersion` нарастает, FK не меняется.
- Связь с RFI — FK `document_request` / `document_response` (D6) + физическое
  положение в папке RFI. Карточка RFI даёт ссылки на оба документа; в карточке
  документа блок «Связанный RFI» — **не добавляется в MVP** (обратный поиск —
  обратной ссылкой по `related_name`, отдельный этап по необходимости).

### 8.3. Права на файлы (D7)

- Загрузка запроса: author/assignee/gip/admin RFI — через `upload_rfi_document`
  (kwarg `skip_section_write_check=True` в `upload_document`; сам сервис уже
  проверил права по матрице RFI). Остальная валидация `upload_document`
  (расширения, размер, ГОСТ-генерация, зоны) работает без изменений.
- Загрузка/регистрация ответа: gip/admin/assignee (ответ Заказчика принимает
  ГИП или назначенный).
- Прямая загрузка в папку RFI через файловый менеджер (в обход карточки RFI)
  остаётся технически возможной для тех, у кого write в COMMON (ГИП/админ) —
  такие документы можно привязать к RFI через `attach_rfi_document`.

## 9. UI (вьюхи и шаблоны)

Все вьюхи — тонкие над сервисами, права проверяет сервис; во вьюхе —
`_ensure_project_access`-эквивалент (`get_accessible_projects`) и гейт создания
(роль из D5). Bootstrap 5 + существующие классы `redesign.css`, без нового CSS;
бейджи статусов — тот же приём `TASK_STATUS_BADGES` (словарь `RFI_STATUS_BADGES`
в views.py: `open` — secondary, `sent` — primary, `answered` — info,
`registered` — success, `closed` — dark, `escalated` — danger).

### 9.1. Вкладка «Запросы информации (RFI)» (?tab=rfi)

- В `project_detail.html` — четвёртая вкладка после «Заданий» (иконка
  `bi-envelope-paper`), бейдж-счётчик = количество не-closed RFI. Видна всем
  участникам (как «Задания»).
- `ProjectDetailView.get()`: при `?tab=rfi` готовит `rfi_list =
  list_rfis(project, user, status_filter)` и `rfi_summary` (счётчики по
  статусам для сегмента).
- Сегмент-контрол фильтров: Все / Открытые (`open+sent+answered+registered+
  escalated`) / Ждут ответа (`sent`) / Эскалированные / Закрытые.
- Таблица: № (RFI-07) | Название | Автор | Ответственный | Срок ответа (красный
  при просрочке) | Статус (бейдж) | Действия (кнопки матрицы 6.1, доступные
  этому пользователю, или «Открыть»).
- Кнопка «Создать запрос информации» — ролям из D5.
- Шаблон: партиал `storage/_rfi_tab.html`, инклюдится в `project_detail.html`
  при `active_tab == 'rfi'` (по образцу панели «Задания»).

### 9.2. Форма создания `/projects/<id>/rfis/new/`

- `RfiCreateView` + шаблон `storage/rfi_form.html`.
- Поля: subject*, reason*, assignee (select из участников, по умолчанию ГИП),
  rfi_due_date (date, подсказка «до какого числа ждём ответ»), файл запроса
  (опционально сразу — иначе загрузить с карточки позже; без файла нельзя
  будет отправить, можно только сохранить открытый RFI).
- Успех → редирект на карточку RFI; сообщения через `messages`.

### 9.3. Карточка RFI `/projects/<id>/rfis/<rfi_id>/`

- `RfiDetailView` + шаблон `storage/rfi_detail.html` (по образцу карточки
  задания, Этап 4).
- Шапка: «RFI-07 — {subject}», статус-бейдж, живой дедлайн («осталось 3 дня» /
  «просрочен на 6 дней», приём `issue_extras.py`).
- Блок «Суть запроса»: `reason`.
- Блок «Документы»: файл запроса (ссылка на документ в ИРД → RFI + кнопка
  «Загрузить/заменить» при пустом) и файл ответа (кнопка «Загрузить ответ» /
  «Выбрать из папки RFI» — select документов папки).
- Панель действий (по матрице 6.1 и правам пользователя):
  «Отправить Заказчику» (open), «Ответ получен» (sent/escalated),
  «Зарегистрировать ИД» (answered — выбор/загрузка файла ответа),
  «Эскалировать» (sent/answered), «Закрыть» (registered/escalated/open, с
  комментарием; из open — обязательный комментарий отмены).
- Блок «История»: таймлайн `RfiStatusLog` (кто, когда, комментарий).

### 9.4. Действия (POST)

- `POST /projects/<id>/rfis/<rfi_id>/action/` — `RfiStatusActionView`
  (`action` ∈ send/answer/escalate/close + `comment`) — тонкая вьюха над
  сервисами, ошибки через `messages` (по образцу `TaskStatusActionView`).
- `POST /projects/<id>/rfis/<rfi_id>/documents/` — `RfiDocumentActionView`
  (`kind` ∈ request/response; либо файл, либо `document_id` для attach).

### 9.5. Блок RFI на «Сводке» (`task_dashboard.html`)

- Под таблицей «Требуют внимания» заданий — блок «Запросы информации (RFI)»:
  4 карточки-счётчика (Ждут ответа / Просрочены / Эскалированы / Всего
  открытых) + мини-таблица «RFI, требующие внимания» (`rfis_requiring_attention`)
  со ссылками на карточки RFI.
- `TaskDashboardView.get()` добавляет в контекст `rfi_summary` и
  `rfi_attention`. Данные — сервисы `rfi_summary_for_gip` /
  `rfis_requiring_attention`.
- Доступ к Сводке не меняется (ГИП/админ).

## 10. API-интеграция с ГИП-Ассистентом

### 10.1. Авторизация (ответ на вопрос Координатора «авторизация по токену?»)

- **Bearer-токен** из существующего реестра: админ компании на `/api-tokens/`
  создаёт токен «ГИП-Ассистент» со scope `tasks:read`; ГИП-Ассистент хранит
  его в своём `.env` (`STREAM_CAD_API_TOKEN=...`) и шлёт
  `Authorization: Bearer <token>` на каждый запрос.
- Компания всегда берётся из `token.company` (изоляция мультитенанта);
  проект ищется по `code` внутри этой компании (`_api_project`,
  external_api/views.py:94–102). Прямые `request.user`-права ГИПа не
  требуются — доступ сервисный, на уровне компании + scope (тот же принцип,
  что у `issues/pending`).
- Без токена / просрочен / отозван → 401; нет scope → 403; проект чужой
  компании → 404; ошибки валидации → 400 в конверте `{"errors": {...}}`.

### 10.2. Новые scopes (`ApiToken.Scope`, external_api/models.py)

- `TASKS_READ = "tasks:read"` — «Чтение заданий и RFI (ГИП-Ассистент)».
- Миграция **не требуется**: `scopes` — `JSONField`, значения — строки.
- Фаза 2 (запись из ГА) — `tasks:sync`, вводится отдельным этапом (10.7).

### 10.3. Эндпоинты (external_api/urls.py)

| Метод и путь | Scope | Назначение |
|---|---|---|
| `GET /api/v1/projects/<code>/tasks/` | `tasks:read` | **Полный список заданий проекта** — для матрицы связей ГА (источник → цель, статусы, сроки). Требование Координатора: «текущий API отдаёт только сводку — этого мало» |
| `GET /api/v1/projects/<code>/rfis/` | `tasks:read` | Список RFI проекта + сводка по статусам. Параметры: `?status=open,sent,escalated` (CSV-список; без параметра — все не-closed + summary по всем), `?attention=1` (только «требующие внимания»: sent-просроченные + escalated) |

Существующий сессионный `/projects/<id>/tasks/dashboard/api/` остаётся как есть
(D8) — он для JS интерфейса. `GET /api/v1/projects/<code>/tasks/` возвращает и
сводку (блок `summary`) тем же составом, что UI-эндпоинт, поэтому отдельный
summary-эндпоинт наружу не нужен.

### 10.4. Формат JSON: `GET /api/v1/projects/<code>/tasks/`

Поля подобраны по `TaskRead` / `CrossTaskTaskRead` ГА (раздел 2.2):

```json
{
  "project": {"code": "2345-12", "name": "Жилой дом по адресу …"},
  "generated_at": "2026-11-16T09:00:00+03:00",
  "count": 14,
  "summary": {
    "total": 14, "overdue": 3, "on_hold": 1, "awaiting_review": 2,
    "by_status": {"DRAFT": 1, "ISSUED": 4, "IN_PROGRESS": 6, "REVIEW": 2, "ACCEPTED": 1}
  },
  "tasks": [
    {
      "id": 12,
      "external_id": null,
      "title": "Исходные данные для раздела КР",
      "description": "…",
      "status": "IN_PROGRESS",
      "status_label": "В работе",
      "ga_status": "IN_PROGRESS",
      "priority": "high",
      "priority_label": "Высокий",
      "source_section": {"code": "АР", "name": "Архитектурные решения"},
      "target_section": {"code": "КР", "name": "Конструктивные решения"},
      "author": {"username": "i.ivanov", "full_name": "Иванов Иван"},
      "assignee": {"username": "p.petrov", "full_name": "Петров Пётр"},
      "due_date": "2026-11-20",
      "is_overdue": false,
      "overdue_days": 0,
      "days_until_deadline": 4,
      "created_at": "2026-11-02T10:15:00+03:00",
      "updated_at": "2026-11-14T18:40:00+03:00",
      "url": "https://stream-cad.ru/projects/12/tasks/12/"
    }
  ]
}
```

Маппинг на их `Task` (инструкция для ГА, фиксируется в ARCHITECTURE.md §6.2):

| Поле ГА (`TaskRead`) | Поле Stream-CAD | Примечание |
|---|---|---|
| `title` | `title` | |
| `description` | `description` | |
| `deadline` | `due_date` | оба — дата |
| `section_code` (от кого) | `source_section.code` | ключ матрицы |
| `to_section_code` (кому) | `target_section.code` | ключ матрицы |
| `status` (`TaskStatus`) | `ga_status` | маппинг ниже; raw-статус — в поле `status` |
| `assignee_id` (UUID) | `assignee.username/full_name` | UUID-пользователей сопоставить нельзя — ГА матчит по имени или хранит ссылки |
| `source` | константа `"stream_cad"` | происхождение записи |
| `is_complex` | нет аналога | ГА трактует как `false` |
| `overdue_days`, `days_until_deadline` | вычисляются | как в `CrossTaskTaskRead` |

Маппинг статусов DesignTask → `ga_status` (`TaskStatus` ГА):

| DesignTask | `ga_status` | Логика |
|---|---|---|
| DRAFT | NOT_STARTED | черновик — ещё не задание |
| ISSUED | NOT_STARTED | выдано, исполнитель не начал |
| IN_PROGRESS | IN_PROGRESS | |
| ON_HOLD | REWORK | заблокировано/приостановлено; у ГА нет HOLD — ближайший REWORK, raw-статус в `status` позволяет уточнить |
| COMPLETED | REVIEW | выполнено, ожидает проверки |
| REVIEW | REVIEW | на проверке |
| ACCEPTED | DONE | принято |
| RETURNED | REWORK | возвращено на доработку |
| CANCELLED | CANCELLED | |

### 10.5. Формат JSON: `GET /api/v1/projects/<code>/rfis/`

```json
{
  "project": {"code": "2345-12", "name": "Жилой дом по адресу …"},
  "generated_at": "2026-11-16T09:00:00+03:00",
  "count": 7,
  "summary": {
    "total": 7, "open": 1, "sent": 3, "answered": 1, "registered": 1,
    "closed": 0, "escalated": 1, "overdue": 2
  },
  "rfis": [
    {
      "id": 3,
      "number": 7,
      "number_display": "RFI-07",
      "external_id": null,
      "title": "Отсутствуют ТУ на водоснабжение",
      "description": "Для выпуска раздела НВК нужны ТУ от водоканала; блокирует расчёт нагрузок.",
      "status": "escalated",
      "status_label": "Эскалирован",
      "ga_rfi_state": "escalated",
      "author": {"username": "i.ivanov", "full_name": "Иванов Иван"},
      "assignee": {"username": "a.gip", "full_name": "Смирнова Анна"},
      "rfi_due_date": "2026-11-10",
      "is_overdue": true,
      "overdue_days": 6,
      "sent_at": "2026-10-28T12:00:00+03:00",
      "answered_at": null,
      "registered_at": null,
      "escalated_at": "2026-11-11T06:00:00+03:00",
      "closed_at": null,
      "request_document": {"code": "RFI-7-Запрос", "name": "RFI-7: Запрос — ТУ на водоснабжение"} ,
      "response_document": null,
      "url": "https://stream-cad.ru/projects/12/rfis/3/"
    }
  ]
}
```

Совместимость с их RFI (Task + `RfiState`):

| Поле ГА | Поле Stream-CAD | Примечание |
|---|---|---|
| `rfi_state` (`RfiState`) | `status` = `ga_rfi_state` | **значения совпадают 1:1**, маппинг не нужен |
| `title` | `title` (= `subject`) | |
| `description` | `description` (= `reason`) | |
| `rfi_due_date` | `rfi_due_date` | |
| `rfi_cause_decision_id` / `rfi_cause_goal_id` | не отдаются (`null`) | причина RFI живёт в ГА; связь их объекта с нашей записью — по `external_id` (фаза 2) |
| `deadline` | не отдаётся | у RFI в СОД один срок — ответ |
| `assignee_id` | `assignee.username/full_name` | как у Task |
| результат «новые ИД» (Premise) | `response_document` | их REGISTERED = Premise с `source_document_id`; наш `registered` = ответ лежит в ИРД → RFI и привязан к RFI |
| `source` | `"stream_cad"` | |

Параметр `?status=` принимает CSV из шести значений RfiState — их агент
забирает «список RFI в статусе escalated» одним вызовом
`GET /api/v1/projects/<code>/rfis/?status=escalated` (требование Координатора);
`?attention=1` — «всё горящее» одним списком.

### 10.6. Сквозной сценарий интеграции

1. Админ СОД создаёт токен «ГИП-Ассистент» (`/api-tokens/`, scope `tasks:read`).
2. ГА кладёт токен в `.env`; проект связан по `code` (проект создан ГА через
   `POST /api/v1/projects/` — уже работает, scope `projects:create` — либо
   шифр вписан вручную).
3. Агент RFI ГА (их 6.12e) опрашивает СОД по расписанию (рекомендация: 5–15
   минут, вписывается в `UserRateThrottle`):
   - `GET /tasks/` → строит матрицу смежников и свои CrossTask
     (`WAITING_INFO` для заблокированных);
   - `GET /rfis/?status=sent,escalated` → мониторинг сроков; `escalated`
     — сигнал «эскалировать/напомнить ГИПу» на их стороне.
4. RFI, созданные в СОД, видны ГА как внешние (`source="stream_cad"`,
   `external_id=null`); RFI, созданные из ГА (фаза 2), несут их UUID в
   `external_id` — ГА находит «свои» записи.

### 10.7. Фаза 2 (за рамками MVP, отдельно спроектировать)

- `POST /api/v1/projects/<code>/rfis/` (scope `tasks:sync`): создание RFI из
  ГА — `title/description/rfi_due_date/external_id`; приём в сервисе
  `create_rfi_from_api` (автор — владелец токена, assignee — ГИП проекта).
- Опционально `PATCH`-события статусов ГА → СОД (только для RFI с их
  `external_id`), по образцу `sync_issues` (fallback по external_id).
- Не входит в текущий этап — чтобы не раздувать патч и не трогать матрицу
  прав записи раньше времени.

## 11. Уведомления и авто-эскалация

- **Notification** (существующая модель `issues.models.Notification`), по
  образцу `_notify_task` (services.py:4940): вербы-ближайшие `assigned`
  (назначен ответственный) и `status_changed` (переходы, эскалация); ссылка
  `url` на карточку RFI; получатели — author, assignee, gip (с проверкой
  `_is_project_participant`). Эскалация уведомляет gip + assignee
  дополнительно к статусу.
- **Email** — существующая Celery-задача `storage.tasks.send_notification_email`
  (SMTP Timeweb, Этап 5): при `escalated` — письмо ГИПу и assignee. Остальные
  переходы — только колокольчик (MVP).
- **Авто-эскалация (beat):** новая задача `storage.tasks.escalate_overdue_rfis`
  (name-константа для `CELERY_BEAT_SCHEDULE`) → сервис `escalate_overdue_rfis`;
  расписание — раз в сутки (по образцу `notify_task_overdue`). При отказе от
  beat-задачи ручная эскалация сохраняет полный цикл (пометка: Celery в
  проекте уже используется Этапом 5, прецедент есть).
- Напоминания «до срока» (за 2 дня / в день срока, как у заданий) — опционально,
  этап R4.

## 12. Состав изменений (для будущих патчей)

| Файл | Изменение |
|---|---|
| `storage/domain/models.py` | + `RfiTask`, `RfiStatusLog` |
| `storage/migrations/0033_rfi_task_stage1.py` | новая миграция |
| `storage/domain/services.py` | + 13 RFI-сервисов/хелперов (раздел 7); `upload_document` + kwarg `skip_section_write_check=False` (D7) |
| `storage/domain/project_templates.py` | `IRD_FOLDERS` + элемент «Запросы информации (RFI)» |
| `storage/adapters/views.py` | + `RfiCreateView`, `RfiDetailView`, `RfiStatusActionView`, `RfiDocumentActionView`; `ProjectDetailView` — ветка `?tab=rfi`; `TaskDashboardView` — контекст RFI; словари `RFI_STATUS_BADGES` |
| `storage/adapters/urls.py` | + 4 маршрута (new/detail/action/documents) |
| `storage/adapters/templates/storage/project_detail.html` | + вкладка «RFI» |
| `storage/adapters/templates/storage/_rfi_tab.html` | новый (таблица вкладки) |
| `storage/adapters/templates/storage/rfi_form.html` | новый |
| `storage/adapters/templates/storage/rfi_detail.html` | новый (карточка) |
| `storage/adapters/templates/storage/task_dashboard.html` | + блок «Запросы информации (RFI)» |
| `external_api/models.py` | + scope `tasks:read` |
| `external_api/serializers.py` | + `ApiTaskReadSerializer`, `ApiRfiReadSerializer` (по образцу существующих) |
| `external_api/views.py` | + `ApiTaskListView`, `ApiRfiListView` (ApiTokenAuthentication + HasScope + throttle) |
| `external_api/urls.py` | + 2 маршрута |
| `storage/tasks.py` | + `escalate_overdue_rfis` (+ опц. напоминания) |
| `sod_core/settings.py` | `CELERY_BEAT_SCHEDULE` + запись |
| `docs/ARCHITECTURE.md` | §3.2 (модели), §5 (сервисы), §6.2 (API-контракты, scopes, маппинги GA) — правило «модели менять только с обновлением ARCHITECTURE.md» |
| `docs/DESIGN_TASKS_SPEC.md` | п. 9 «RFI и ГИП-Ассистент» (кратко, ссылка на план RFI) |
| `docs/MASTER_SPEC.md`, `docs/PROGRESS.md`, `docs/HANDOFF.md` | обновления по факту закрытия этапов |
| `storage/tests/test_rfi_services.py`, `test_rfi_views.py`, `test_rfi_api.py` | новые (раздел 14) |

## 13. Этапы реализации для Кодера

Каждый этап — отдельный `.patch` (формат Этапов 1–6), база — актуальный `main`:

| Этап | Состав | Критерий готовности |
|---|---|---|
| **R1. Модель и сервисы** | Модели + миграция 0033; все сервисы раздела 7; kwarg `upload_document`; `IRD_FOLDERS`; тесты `test_rfi_services.py` | Матрица переходов полностью покрыта тестами; `makemigrations --check` — без новых миграций кроме 0033; регресс `storage.tests` зелёный |
| **R2. UI** | Вкладка `?tab=rfi`, форма, карточка, действия, документы; блок RFI на «Сводке»; тесты `test_rfi_views.py` | Полный цикл «создал → отправил → получил ответ → зарегистрировал → закрыл» и эскалация выполняются из интерфейса; файлы лежат в ИРД → RFI (правило «НЕТ UI — НЕТ ФИЧИ») |
| **R3. API** | Scope `tasks:read`; 2 эндпоинта + сериализаторы; ARCHITECTURE.md §6.2 (контракты, маппинги, инструкция для ГА); тесты `test_rfi_api.py` | ГА может по токену получить полный список заданий и escalated-RFI в формате раздела 10; изоляция компаний проверена тестами |
| **R4 (опционально).** Автоматика | Beat-автоэскалация, напоминания о сроках, email | Просроченные `sent` эскалируются без ручного действия; письма доходят |

## 14. План тестов

- **test_rfi_services.py (~28):** нумерация и гонка `number`; каждый переход
  матрицы — успех и запрет (не тот статус / не тот пользователь); права D5 по
  ролям (gip/admin/developer/contractor/viewer/customer); `assignee` по
  умолчанию; `upload_rfi_document` — валидация файла, права, шифр, папка
  создаётся лениво; `register_rfi_response` — документ чужого проекта / не из
  папки RFI отклоняется; `close_rfi` из open без комментария; сводка и
  `rfis_requiring_attention` (пересечения и сортировка); `escalate_overdue_rfis`.
- **test_rfi_views.py (~16):** вкладка рендерится участнику и скрывается от
  посторонних (404 вне компании); создание по ролям (403 viewer/customer);
  карточка, действия по матрице, сообщения об ошибках сервиса; загрузка
  документа в папку RFI и attach; блок RFI на Сводке (доступ ГИП/админ,
  403 прочим).
- **test_rfi_api.py (~14):** 401 без/с невалидным/просроченным токеном; 403 без
  scope `tasks:read`; 404 проект чужой компании; состав JSON (ключи `ga_status`,
  `source_section`, `rfi_state`-совпадение статусов); `?status=escalated`;
  `?attention=1`; `count`/`summary` согласованы; токен отозван → 401.

## 15. Риски и ограничения

1. **Kwarg в `upload_document` (D7)** — единственное касание существующей
   функции; обратно совместим (default `False`), существующие вызовы не меняют
   поведения. Покрыть двумя тестами (kwarg=False — прежняя проверка, True —
   пропуск только section-проверки; zone-проверки остаются).
2. **COMMON-раздел:** RFI-документы живут в `(project, COMMON)`; после
   sections-sync COMMON — контейнер нераспознанных ИРД-папок, папка RFI не
   конфликтует с марками. Проверить при R1, что `sync_project_sections` не
   трогает папку RFI (тест).
3. **Целостность папки:** сервис не обязан защищать папку RFI от удаления
   пользователем; `get_or_create_rfi_folder` — идемпотентное восстановление
   при любом обращении. Ограничение documented, защита от удаления папки —
   за рамками MVP.
4. **Celery-ограничение MASTER_SPEC** (п. 5: «вне ИИ-контура не используются»)
   против прецедента Этапа 5 — beat-эскалация оформляется как Этап 5
   (`storage/tasks.py` + `CELERY_BEAT_SCHEDULE`); если Координатор сочтёт
   недопустимым, R4 отключается, ручная эскалация сохраняет цикл.
5. **`external_id` фазы 2:** пока ГА не пишет в СОД, поле всегда `null` — не
   блокирует MVP, индекс заложен сразу.
6. **Технические имена с «dashboard»** — не пользовательские тексты, не
   переименовываются в MVP (раздел 3).
7. **Нумерация `number`:** `max+1` при конкурентном создании может упереться в
   `unique_together` — сервис повторяет попытку (как стандартный приём для
   последовательных номеров), тест на гонку.

## 16. Чек-лист жёстких требований Координатора

| Требование | Как закрыто |
|---|---|
| 1. RFI — отдельная сущность, не флаг на DesignTask; статусы open/sent/answered/registered/closed/escalated и свой жизненный цикл | D1, D2, разделы 5–7: модель `RfiTask` + `RfiStatusLog`, матрица 9 переходов, 7 сервисов |
| 2. Файлы RFI — в подпапке «Запросы информации (RFI)» внутри «Исходные данные (ИРД)»; запросы и ответы Заказчика складываются туда | Раздел 8: `IRD_FOLDERS` + ленивое создание; запрос и ответ — Document в папке, шифры RFI-N-Запрос/RFI-N-Ответ |
| 3. «Дашборд» не используем — только «Сводка» | Раздел 3: все пользовательские тексты — «Сводка», «Запросы информации (RFI)»; технические идентификаторы — осознанный бэклог |
| 4. API: как ГА вызывает, авторизация по токену, формат JSON, дополнительные данные (список escalated) | Раздел 10: Bearer `tasks:read`, изоляция по компании; `GET /api/v1/projects/<code>/tasks/` — **полный список** для матрицы; `GET /api/v1/projects/<code>/rfis/?status=escalated`; JSON подобран по их DTO/сущностям, статусы RFI совпадают 1:1 |
| Модель RfiTask с полями project, author, assignee, status, rfi_due_date, document_request, document_response, reason | Раздел 5.1: все 8 полей есть; дополнения (subject, number, external_id, таймстампы) обоснованы в D3 |
| Сервисы create_rfi, send_rfi, answer_rfi, close_rfi + матрица переходов | Разделы 6–7 (плюс register_rfi_response, escalate_rfi — покрытие матрицы) |
| UI: отдельная вкладка «Запросы информации (RFI)» | Раздел 9.1–9.4 (?tab=rfi, форма, карточка, действия) |
| Связь ответов Заказчика с папкой ИРД → RFI | Раздел 8.2–8.3: загрузка через `upload_rfi_document`, FK `document_response`, attach из папки |
