# План: интеграция Stream-CAD ↔ ГИП-Ассистент через S3 Data Lake («s3-sync»)

**Роль:** Аудитор (проект Stream-CAD, репозиторий `ABEL678/sod-project`, база HEAD = f0514da).
**Статус:** проект плана — только проектирование. Код не пишется, коммиты не делаются.
**Принятое решение Координатора:** интеграция с ГИП-Ассистентом — через общее
Data Lake на S3 (Cloud.ru Evolution Object Storage), а не через прямые API-вызовы:
БД у продуктов разные (у Stream-CAD — Django/PostgreSQL с int-PK, у ГИП-Ассистента —
FastAPI/SQLAlchemy с UUID-PK), и связывать их напрямую очередью HTTP-вызовов
дорого и хрупко.

**Исследование, на которое опирается план:** репозиторий
`https://github.com/ABEL678/gip-assistant` изучен (модели `Task`, `RFI`, сервисы
`RfiService`, `MatrixService`, `docs/specs/MASTER_SPEC.md`, `docs/context/DOMAIN.md`,
`.env.example`). Все выводы о формате данных ниже — из их кода, с указанием файлов.

---

## 1. Резюме (TL;DR)

| Вопрос задачи | Решение |
|---|---|
| Структура бакета | Один бакет, префикс `projects/{slug-проекта}/`, два под-префикса: `outbox/` (пишет Stream-CAD) и `inbox/` (пишет ГИП-Ассистент). В outbox — `tasks.json`, `rfis.json`, `metadata.json` (манифест с чексуммами и справочником разделов) |
| Формат файлов | Снапшоты — обычный JSON с envelope (`schema_version`, `generated_at`, `items[]`). Команды во входящих — по одному JSON-файлу на команду (не JSONL; обоснование в §3.2) |
| Выгрузка | Celery-задача `sync_project_to_s3` (имя `storage.tasks.sync_project_to_s3`): собирает снапшот из БД, кладёт 3 файла через boto3. Beat: каждые 15 минут по «грязным» проектам + полный пересинк ежедневно в 02:30. «Грязность» — флаг `dirty` в новой модели `SyncState`, выставляется сигналами `post_save/post_delete` на `DesignTask` |
| Входящие | Celery-beat `process_s3_inbox` каждые 5 минут: `list_objects_v2` по `inbox/commands/`, валидация, применение команды (allowlist типов), перенос файла в `done/` или `failed/` |
| Настройки | `S3_ENDPOINT`, `S3_BUCKET_NAME`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION`, `S3_SYNC_ENABLED` — по существующей конвенции `os.environ.get` |
| Безопасность | Два независимых комплекта ключей IAM: у Stream-CAD — write в `outbox/` + read в `inbox/`, у ГИП-Ассистента — зеркально. Плюс allowlist типов команд, лимит размера, sha256-манифест, SSE-шифрование бакета |
| Новые модели | `SyncState`, `SyncLog`, `InboxCommand` (миграция `0034_s3_sync_models`; `0033` занята `0033_rfi_task_stage1`) |
| Зависимости | `boto3` — новая зависимость requirements.txt; `moto` — dev-зависимость для тестов |
| Зависимость от RFI-модуля | `tasks.json` раскатывается без RFI-модуля (в `rfis.json` — пустой `items[]`). Полноценный `rfis.json` требует `RfiTask` — отдельный план `RFI_MODULE_PLAN.md` |

Терминология: в документе и в будущих реализациях используется слово
«Сводка» (не «Дашборд») — по решению Координатора, принятому в Этапе 6.

---

## 2. Диагностика с пруфами

### 2.1. Что уже есть в Stream-CAD (якоря, HEAD = f0514da)

| Якорь | Где | Что даёт s3-sync |
|---|---|---|
| `DesignTask` | `storage/domain/models.py:697` | Источник данных для `tasks.json`: `source_section`/`target_section` (FK `Section`), 9-статусная машина, `due_date`, `external_id` (уже задуман под ГИП-Ассистент) |
| `Section` | `storage/domain/models.py:68` | `code` (≤20, уникален в проекте) — прямой аналог `section_code`/`to_section_code` ГИП-Ассистента (у них `String(50)`) |
| `Project` | `storage/domain/models.py:32` | `code` (уникальный шифр, ≤50) — ключ папки в бакете |
| API Сводки | `storage/adapters/views.py:1953`, `TaskDashboardApiView` | Существующий JSON-эндпоинт возвращает только сводку (`summary` + `tasks_requiring_attention`) — для матрицы связей этого недостаточно, что и подтверждено Координатором |
| external_api (Этап 14) | `external_api/` | `ApiToken` + Bearer + scopes: готовый канал для ручных проверок; Data Lake становится основным каналом интеграции (см. §3.8) |
| Celery-конвенция | `storage/tasks.py`, `sod_core/settings.py:306` | Именованные задачи `storage.tasks.*`, тонкие обёртки над сервисами («гексагон лайт»), `CELERY_BEAT_SCHEDULE` в settings |
| Порт/адаптер | `storage/ports/file_storage.py`, `storage/adapters/local_storage.py` | Готовый паттерн для S3-клиента: порт в `storage/ports/`, реализация в `storage/adapters/` |
| Сигналы | `storage/domain/signals.py` | Точка подключения dirty-маркеров (`post_save` уже используется для `UserProfile`) |
| Cloud.ru уже используется | `sod_core/settings.py:160-177` (`AI_EMBEDDING_*`, базовый URL `foundation-models.api.cloud.ru`) | Организационно Cloud.ru уже задействован — объектное хранилище Evolution того же провайдера |
| Зависимости | `requirements.txt:39-40` | `celery==5.7.0`, `redis` есть; **`boto3` отсутствует** — новая зависимость |

### 2.2. Формат, который ожидает ГИП-Ассистент (из их репозитория)

Модель `Task` (`backend/src/adapters/db/models/task.py`):

- `id` — UUID (PK), `project_id` — UUID (FK projects);
- `title` — `String(500)` (наш `title` ≤255 влезает);
- `description` — Text;
- `status` — `TaskStatus`: `NOT_STARTED`, `IN_PROGRESS`, `REVIEW`, `REWORK`, `DONE`, `CANCELLED` (верхний регистр);
- `assignee_id` — UUID FK на их таблицу `executors` (наши пользователи им неизвестны → отдаём `null` + имя в расширении);
- `deadline` — `Date` (дата без времени);
- `section_code` — `String(50)`, «от кого (источник задания)»;
- `to_section_code` — `String(50)`, «кому (получатель задания)»;
- `sort_order` — int; `source` — `String(20)`, по умолчанию `"manual"`;
- `is_complex` — bool; `decision_id` — UUID nullable;
- RFI-поля: `is_rfi` — bool, `rfi_cause_decision_id`, `rfi_cause_goal_id` (XOR-ограничение `ck_tasks_rfi_cause`), `rfi_due_date` — `Date`, `rfi_state` — `RfiState`;
- `RfiState`: `open`, `sent`, `answered`, `registered`, `closed`, `escalated` — **строчными** (их двойная enum-конвенция: `TaskStatus` — верхний регистр через `.name`, `RfiState` — нижний через `.value`; зафиксировано в их `MASTER_SPEC.md` §5.1 как долг);
- `created_at`, `updated_at` — timestamps (у них `TimestampMixin`).

Ключевые сервисы:

- `MatrixService` (`backend/src/domain/services/matrix_service.py`): матрица смежников
  строится из **фактических пар** `(section_code → to_section_code)` по ВСЕМ задачам
  проекта (`get_by_project(project_id, limit=5000)`), агрегирует статусы, считает
  просрочки по `deadline`. Отсюда требование Координатора: нужен **полный список**
  заданий, а не сводка.
- `RfiService` (`backend/src/domain/services/rfi_service.py`): `create()` валидирует
  XOR причины (`rfi_cause_decision_id XOR rfi_cause_goal_id`), ставит `is_rfi=True`,
  статус задачи `NOT_STARTED`, `rfi_state` по умолчанию `OPEN`; `escalate()` просто
  переводит `rfi_state = ESCALATED`.
- `CrossTaskTaskRead` (`backend/src/application/dto/task.py`): к задаче в контексте
  матрицы добавляются вычисляемые `overdue_days` и `days_until_deadline`.
- Онтология (`docs/specs/MASTER_SPEC.md` §1.2, §2.1): `Result → Goal → Decision →
  Task(is_rfi=true) → Action`; результат RFI — `Document`, из него — новые `Premise`
  (вводные). В их БД **отдельной таблицы `rfis` нет** — RFI это Task с `is_rfi=true`
  (решение 6.12a).
- `RfiProposal` (`backend/src/application/dto/rfi.py`): эфемерное предложение
  `create|escalate` с `formulation`, `rationale`, `rfi_due_date` — их агент RFI
  (6.12e) генерирует такие предложения; в Data Lake-сценарии их агент сможет
  адресовать их нам (команда `rfi.proposal`, §3.5).
- `.env.example` ГИП-Ассистента: настроек S3/MinIO **нет** (только DB, JWT,
  Telegram, CORS, LLM). Значит импортер Data Lake им предстоит строить — наш план
  фиксирует контракт (§3.3) как основу для их импортера.

### 2.3. Несопоставимое напрямую и способы согласования

| Расхождение | Stream-CAD | ГИП-Ассистент | Решение |
|---|---|---|---|
| Первичный ключ | int (Django) | UUID (SQLAlchemy) | Стабильные UUID-идентификаторы `uuid5` (детерминированные, обратимые, без миграций) — §3.3.4 |
| Статус задачи | 9 состояний | 6 состояний | Таблица маппинга — §3.3.2; наш сырой статус всегда передаётся в расширении |
| Идентификатор проекта | `Project.code` (шифр) | UUID | `metadata.json` несёт и `code`, и uuid5-проекта; их импортер сопоставляет |
| Исполнитель | `User` (наш) | UUID `executors` | `assignee_id = null`, ФИО/логин в расширении `assignee_name` |
| Причина RFI | связанный `DesignTask` | `Decision` XOR `Goal` | Их строгие поля отдаём `null`; причину несёт расширение `rfi_cause_design_task_id` — **NOT IMPLEMENTED**, вариант выбирает Координатор, §3.3.3 |
| Время | `USE_TZ=True` | `created_at/updated_at` | ISO 8601 в UTC (`...Z`) в JSON |

---

## 3. Целевая модель

### 3.1. Топология бакета (пункт 1 задачи)

Один бакет (`S3_BUCKET_NAME`), внутри — по папке на проект:

```
s3://{S3_BUCKET_NAME}/
  projects/{project_slug}/
    outbox/                      # ВЛАДЕЛЕЦ ЗАПИСИ: Stream-CAD. Читает ГИП-Ассистент.
      tasks.json                 # полный список заданий (DesignTask)
      rfis.json                  # полный список запросов информации (RfiTask)
      metadata.json              # манифест: generation, чексуммы, справочник разделов
    inbox/
      commands/                  # ВЛАДЕЛЕЦ ЗАПИСИ: ГИП-Ассистент. Разбирает Stream-CAD.
        1_ack.import_{command_id}.json
        2_rfi.proposal_...json
      done/                      # переносит Stream-CAD после успешной обработки
      failed/                    # переносит Stream-CAD после 3 неудачных попыток
```

Правила:

1. **`project_slug`** — `Project.code`, приведённый к безопасному набору
   `[A-Za-z0-9._-]` (S3-ключи с пробелами/кириллицей работают, но создают трудности
   в CLI и у импортеров). Если код уже безопасен — slug равен коду; если пришлось
   заменять символы — добавляется детерминированный суффикс
   `-u{uuid5(code).hex[:8]}` для гарантии уникальности. Оригинальный шифр всегда
   лежит в `metadata.json.project.code` — только он является юридической связкой.
2. **Только три файла в outbox.** Справочник разделов (для их матрицы) кладём в
   `metadata.json.sections`, а не отдельным файлом — файлов ровно столько, сколько
   назвал Координатор.
3. **Порядок записи и целостность.** S3 PUT атомарен (клиент видит объект целиком:
   старую или новую версию). Порядок выгрузки: `tasks.json` → `rfis.json` →
   `metadata.json`. Манифест пишется последним и содержит sha256 и размер каждого
   файла: читатель (импортер ГИП-Ассистента) сверяет чексуммы прочитанных файлов
   и при несовпадении перечитывает — это закрывает гонку «прочитал tasks.json
   нового поколения, а rfis.json ещё старого».
4. **Поколения.** Каждая выгрузка увеличивает `generation` (monotonic int в
   `SyncState`). Импортер ГИП-Ассистента сверяет `generation` из манифеста с
   последним импортированным и с нашей последней квитанцией (`ack.import`).
5. **Серверное версионирование бакета** — включить (защита от случайной перезаписи;
   Cloud.ru Evolution поддерживает версионирование объектов в S3-совместимом
   режиме; уточнить при настройке бакета).

### 3.2. Формат файлов: JSON vs JSON Lines (пункт 1 задачи)

| Критерий | Обычный JSON (снапшот целиком) | JSON Lines |
|---|---|---|
| Семантика данных | Полный снимок состояния: перезапись целиком, сверка чексумм | Поток событий: дописывание строк |
| Возможности S3 | PUT атомарен, идеален | Append в S3 невозможен: «дописать строку» = прочитать объект целиком, изменить, положить обратно — гонка при двух писателях |
| Частичное повреждение | Файл целиком невалиден, но его перезапишет следующая выгрузка | Одна битая строка может потеряться в середине; нужен offset-курсор |
| Валидация на стороне читателя | Схема на весь документ | Схема на строку + курсор |
| Отладка глазами | Удобно (одна структура) | Хуже для снапшотов |

**Решение:**

- Снапшоты (`tasks.json`, `rfis.json`, `metadata.json`) — **обычный JSON**:
  данные по своей природе — снимок состояния проекта; перезапись целиком проще,
  атомарна и не требует курсоров.
- Входящие команды — **не JSONL, а по одному JSON-файлу на команду** в
  `inbox/pending/`. Причины: (а) append в S3 всё равно эмулируется чтением
  целиком — файл-за-команду атомарен без гонок; (б) разбор = `list_objects_v2`
  + обработка + перенос файла — отработанный паттерн очереди на S3; (в) идемпотентность
  по `command_id` + порядок по `seq`; (г) poison-сообщение изолируется переносом
  в `failed/`, не блокируя очередь.

### 3.3. Контракты JSON (пункт 1 задачи, формат данных)

#### 3.3.1. `tasks.json` — полный список заданий для матрицы

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-11-16T06:30:00Z",
  "generation": 42,
  "source_system": "stream-cad",
  "project_code": "123-45.67",
  "items": [
    {
      "id": "3f8a1c2e-...-uuid5",
      "project_id": "7b2d...-uuid5",
      "title": "Выдать данные по нагрузкам на ВК",
      "description": "Полный текст задания...",
      "status": "IN_PROGRESS",
      "assignee_id": null,
      "deadline": "2026-11-20",
      "section_code": "ОВ",
      "to_section_code": "ВК",
      "sort_order": 0,
      "source": "stream_cad",
      "is_complex": false,
      "decision_id": null,
      "is_rfi": false,
      "rfi_cause_decision_id": null,
      "rfi_cause_goal_id": null,
      "rfi_due_date": null,
      "rfi_state": null,
      "premise_ids": [],
      "created_at": "2026-11-10T09:15:00Z",
      "updated_at": "2026-11-15T12:00:00Z",

      "stream_cad_id": 123,
      "stream_cad_status": "IN_PROGRESS",
      "stream_cad_priority": "high",
      "assignee_name": "Иванов И.И.",
      "overdue_days": 0,
      "days_until_deadline": 4
    }
  ]
}
```

Правила по полям (совместимость с их `Task` — ключевой принцип: **каждая запись
валидируется их pydantic-моделью `TaskRead` без ошибок**; `TaskRead` игнорирует
незнакомые поля, поэтому наши расширения `stream_cad_*` безопасны):

| Их поле | Наш источник | Правило |
|---|---|---|
| `id` | uuid5 от `design-task:{project.code}:{pk}` | §3.3.4 |
| `project_id` | uuid5 от `project:{project.code}` | §3.3.4 |
| `title` | `DesignTask.title` | ≤255 — влезает в их `String(500)` |
| `description` | `DesignTask.description` | как есть |
| `status` | маппинг из `DesignTask.status` | §3.3.2 |
| `assignee_id` | `null` | наши UUID пользователей не их `executors`; имя — в `assignee_name` |
| `deadline` | `DesignTask.due_date` | `YYYY-MM-DD` или `null` |
| `section_code` | `DesignTask.source_section.code` | источник задания |
| `to_section_code` | `DesignTask.target_section.code` | получатель задания |
| `sort_order` | 0 | у нас сортировки нет; поле обязательно у них |
| `source` | `"stream_cad"` | их `String(20)`; в их UI видно происхождение |
| `is_complex` | `false` | у нас нет аналога |
| `decision_id` | `null` | их онтология Decision нам не принадлежит |
| `is_rfi` | `false` | задания — не RFI |
| `created_at`/`updated_at` | `DesignTask.created_at/updated_at` | ISO 8601 UTC |

Расширения (уникальные для Stream-CAD, их pydantic их проигнорирует):

| Поле | Смысл |
|---|---|
| `stream_cad_id` | int PK `DesignTask` — для обратных команд (`task.deadline_update`) |
| `stream_cad_status` | наш сырой статус (`DRAFT`/`ISSUED`/`ON_HOLD`/...) — сохраняет точность маппинга |
| `stream_cad_priority` | наш `priority` (`low`/`normal`/`high`/`critical`) |
| `assignee_name` | ФИО исполнителя (или `null`) |
| `overdue_days` / `days_until_deadline` | те же вычисляемые поля, что у них в `CrossTaskTaskRead`, — считаем на нашей стороне, чтобы их импортер не пересчитывал |

Состав выгрузки: **все** задания проекта, включая `DRAFT` и `CANCELLED`
(полный список для матрицы, как требует Координатор). Их `MatrixService`
сам отфильтрует нужное; точность различий — в `stream_cad_status`.

#### 3.3.2. Маппинг статусов задания (9 → 6)

| `DesignTask.status` | → их `status` | Обоснование |
|---|---|---|
| `DRAFT` | `NOT_STARTED` | черновик ещё не выдан, работы не начаты |
| `ISSUED` | `NOT_STARTED` | выдано получателю, но он ещё не начал |
| `IN_PROGRESS` | `IN_PROGRESS` | прямое соответствие |
| `ON_HOLD` | `NOT_STARTED` | работа фактически не идёт; отличать по `stream_cad_status` (см. «точки решения», §6.2) |
| `REVIEW` | `REVIEW` | прямое соответствие |
| `RETURNED` | `REWORK` | «возвращено на доработку» = их «на доработке» |
| `COMPLETED` | `DONE` | выполнено (ещё не принято в СОД — но для их матрицы готово) |
| `ACCEPTED` | `DONE` | принято; различие сохраняется в `stream_cad_status` |
| `CANCELLED` | `CANCELLED` | прямое соответствие |

Их `MatrixService._aggregate_status` ориентируется на `DONE`, `REWORK`,
`IN_PROGRESS`, просрочки — все четыре наших значения маппинга покрывают эту
логику корректно.

#### 3.3.3. `rfis.json` — список запросов информации (особенно escalated)

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-11-16T06:30:00Z",
  "generation": 42,
  "source_system": "stream-cad",
  "project_code": "123-45.67",
  "items": [
    {
      "id": "9c1d...-uuid5",
      "project_id": "7b2d...-uuid5",
      "title": "Запрос ТУ на подключение к сетям водоканала",
      "description": "Формулировка запроса: требуется ...",
      "status": "IN_PROGRESS",
      "assignee_id": null,
      "deadline": "2026-11-25",
      "section_code": "ОВ",
      "to_section_code": null,
      "sort_order": 0,
      "source": "stream_cad",
      "is_complex": false,
      "decision_id": null,
      "is_rfi": true,
      "rfi_cause_decision_id": null,
      "rfi_cause_goal_id": null,
      "rfi_due_date": "2026-11-25",
      "rfi_state": "escalated",
      "premise_ids": [],
      "created_at": "2026-11-08T10:00:00Z",
      "updated_at": "2026-11-15T16:40:00Z",

      "stream_cad_number": "РИ-007",
      "stream_cad_status": "escalated",
      "assignee_name": "Петрова А.А.",
      "rfi_cause_design_task_id": "3f8a1c2e-...-uuid5",  // NOT IMPLEMENTED, см. статус ниже
      "escalated_at": "2026-11-15T16:40:00Z",
      "sent_at": "2026-11-09T11:30:00Z",
      "answered_at": null,
      "registered_at": null,
      "closed_at": null,
      "request_document": {
        "code": "ИРД/RFI/РИ-007-запрос.pdf",
        "name": "РИ-007-запрос.pdf",
        "size_bytes": 148932,
        "sha256": "ab12...",
        "updated_at": "2026-11-09T11:29:00Z"
      },
      "response_document": null,
      "linked_tasks": ["3f8a1c2e-...-uuid5"]  // NOT IMPLEMENTED, см. статус ниже
    }
  ]
}
```

> **Статус полей `rfi_cause_design_task_id` и `linked_tasks`: NOT IMPLEMENTED.
> Решение не принято; выбор варианта A или B — за Координатором.**
>
> Факт: модель `RfiTask` (`storage/domain/models.py:950`) не содержит ни FK на `DesignTask`,
> ни M2M на него; миграция `0033_rfi_task_stage1` их не создаёт. Сейчас
> `storage/domain/sync/mapping.py:212-213` выдаёт оба поля константами `null` / `[]`.
>
> - **Вариант A — убрать из контракта.** Удалить оба поля из примера выше, из правил ниже,
>   из §2.2 и §5. Связь RFI → задание в Data Lake не передаётся; при необходимости — отдельный
>   план. Для реализации требуется правка `mapping.py` (вне этого шага).
> - **Вариант B — оставить как планируемые.** Требуется миграция: FK `RfiTask.cause_design_task`
>   (null=True, на `DesignTask`) для `rfi_cause_design_task_id`; для `linked_tasks` — M2M или
>   обратный FK «RFI блокирует задания». До миграции `mapping.py` выдаёт `null` / `[]`.
>   Миграция требует обновления `docs/ARCHITECTURE.md` (раздел 3.2 не содержит `RfiTask`).
>
> Пока решение не принято, оба поля в контракте считаются непроверенными и не входят в
> обязательный набор полей для приёмки (Этап E).

Правила:

- **`rfi_state` 1:1 с их `RfiState`.** Наши шесть состояний RFI (`open, sent,
  answered, registered, closed, escalated`) — ровно их значения (нижний регистр).
  Это осознанное согласование при проектировании `RfiTask` (см. §5, зависимость).
- `status` (их `TaskStatus`) при экспорте RFI: `NOT_STARTED` для `open`,
  `IN_PROGRESS` для `sent`/`escalated` (работа по получению ответа идёт),
  `DONE` для `closed`. Точная таблица — в `RFI_MODULE_PLAN.md`; здесь фиксируется
  требование: поле обязано присутствовать и быть валидным значением их enum.
- `rfi_cause_decision_id` / `rfi_cause_goal_id` = `null`: их XOR-ограничение
  `ck_tasks_rfi_cause` действует в **их** БД; наш RFI проистекает из задания
  смежнику, а не из их `Decision`. Причину несёт расширение
  `rfi_cause_design_task_id` (**NOT IMPLEMENTED**, см. статус выше; вариант A/B — за Координатором). Их импортеру
  при создании локальной копии RFI придётся либо сопоставить причину с их
  `Decision`, либо ослабить ограничение для импортированных строк (`source =
  'stream_cad'`) — это их выбор, мы фиксируем его в контракте как рекомендацию.
- `request_document` / `response_document` — **метаданные** документов из папки
  «Исходные данные (ИРД) → Запросы информации (RFI)» (код документа, имя,
  размер, sha256, время обновления). Сами файлы в Data Lake MVP **не выгружаются**:
  они остаются в СОД Stream-CAD, куда отвечает Заказчик. Опция следующего этапа —
  зеркалирование файлов в `projects/{slug}/outbox/documents/{РИ-xxx}/`
  (см. §7, этап F).
- `linked_tasks` — **NOT IMPLEMENTED** (см. статус выше; вариант A/B — за Координатором); uuid5 заданий, заблокированных этим запросом (ГИП-Ассистент
  получает возможность построить связку «RFI блокирует задачи матрицы»).
- Состав выгрузки: все RFI проекта; приоритет Координатора — «особенно escalated»
  — покрывается тем, что escalated присутствует в общем списке со своим
  `rfi_state` (их агент RFI фильтрует по нему, как их `list_overdue`).

#### 3.3.4. Стабильные UUID-идентификаторы (uuid5)

Обе системы пользуются UUID; наш PK — int. Решение: детерминированные
`uuid5` от строковых ключей, без миграций и без синхронизационной таблицы:

```
NAMESPACE = uuid5(uuid.NAMESPACE_URL, "https://stream-cad.ru/s3-sync/v1")

project      = uuid5(NAMESPACE, f"project:{project.code}")
design_task  = uuid5(NAMESPACE, f"design-task:{project.code}:{task.pk}")
rfi          = uuid5(NAMESPACE, f"rfi:{project.code}:{rfi.pk}")
```

Свойства: (а) стабильно между выгрузками (uuid5 — чистая функция); (б) обратимо
на нашей стороне без парсинга: при обработке входящей команды мы пересчитываем
uuid5 для всех объектов проекта (их десятки/сотни) и находим объект по совпадению;
(в) `project.code` — уникальный ключ, поэтому коллизии между проектами исключены;
(г) переименование `Project.code` меняет все uuid5 — поэтому шифр проекта
запрещено менять при включённом s3-sync (проверка в сервисе смены шифра;
точка решения §6.5).

#### 3.3.5. `metadata.json` — манифест

```json
{
  "schema_version": "1.0",
  "source_system": "stream-cad",
  "generation": 42,
  "generated_at": "2026-11-16T06:30:00Z",
  "project": {
    "code": "123-45.67",
    "name": "Жилой комплекс на Проспекте",
    "customer": "ООО «Заказчик»",
    "gip_username": "gip.ivanov",
    "status": "active"
  },
  "sections": [
    {"code": "АР", "name": "Архитектура", "full_code": "АР-01"},
    {"code": "ОВ", "name": "Отопление и вентиляция", "full_code": "ОВ-01"}
  ],
  "counts": {"tasks": 118, "rfis": 7, "rfis_escalated": 2},
  "files": {
    "tasks.json": {"sha256": "...", "bytes": 148000},
    "rfis.json":  {"sha256": "...", "bytes": 9000}
  },
  "sync_state": {
    "last_ack_generation": 41,
    "last_ack_at": "2026-11-16T05:05:00Z"
  }
}
```

- `sections` — справочник `Section` проекта: их импортер сверяет
  `section_code`/`to_section_code` задач со справочником и получает имена
  разделов для своей матрицы.
- `counts` — быстрые валидаторы для их импортера (совпадает ли количество
  прочитанных строк с заявленным).
- `files` — sha256 и размер: проверка целостности и защита от гонки чтения (§3.1).
- `sync_state.last_ack_generation` — последняя квитанция, полученная нами от
  ГИП-Ассистента (см. §3.5, `ack.import`): обе стороны видят, где стоит конвейер.

### 3.4. Выгрузка в S3 (пункт 2 задачи)

#### 3.4.1. Слои

```
storage/ports/s3_gateway.py        # порт: put_json_object, get_json_object,
                                   #       list_keys, move_object, delete_object
storage/adapters/s3_boto3.py       # реализация на boto3.client("s3", endpoint_url=...)
storage/domain/sync/mapping.py     # uuid5, маппинг статусов, DesignTask → dict,
                                   #   RfiTask → dict (чистые функции, без I/O)
storage/domain/sync/export.py      # build_tasks_payload(project),
                                   #   build_rfis_payload(project),
                                   #   build_metadata(project, files),
                                   #   export_project_to_s3(project)  ← фасад
storage/domain/sync/inbox.py       # process_project_inbox(project), apply_command()
storage/tasks.py                   # тонкие Celery-обёртки (конвенция проекта)
```

Соответствует «гексагон лайт»: логика — в `storage/domain/`, инфраструктура —
адаптер, порты — `storage/ports/`. boto3-клиент — единственное место, где
знают про `S3_ENDPOINT`/ключи.

#### 3.4.2. Celery-задачи и расписание (имена — конвенция `storage.tasks.*`)

| Задача (name) | Расписание (CELERY_BEAT_SCHEDULE) | Что делает |
|---|---|---|
| `storage.tasks.sync_all_projects_to_s3` | каждые 15 минут | выбирает проекты: `SyncState.dirty=True` ИЛИ `last_success_at IS NULL` (новые); на каждый — `sync_project_to_s3.delay(project_id)` |
| `storage.tasks.sync_project_to_s3` | по требованию (delay) + вручную | снапшот проекта → 3 файла в `outbox/` → `SyncState` (generation+1, dirty=False, last_success_at) → `SyncLog` |
| `storage.tasks.sync_s3_full` | ежедневно 02:30 | полный пересинк всех проектов с `SyncState.enabled=True` (защита от потерянных dirty-маркеров) |
| `storage.tasks.process_s3_inbox` | каждые 5 минут | для каждого проекта с `enabled=True`: разобрать `inbox/commands/` (§3.5) |
| `storage.tasks.cleanup_sync_logs` | еженедельно, вс 03:00 | удалить `SyncLog` старше 90 дней |

Почему «каждые 15 минут» для выгрузки: ГИП-Ассистент живёт в суточном цикле
ГИПа (их агенты — напоминания и эскалации), задержка данных до 15 минут для
матрицы и RFI приемлема; при этом выгрузка снапшота дешёвая (один SELECT по
двум таблицам + один PUT на файл). Событийная отправка «сразу после сохранения»
(по сигналу) сознательно не выбирается: (а) при выдаче 20 заданий подряд было
бы 20 PUT вместо одного через 15 минут; (б) сигналы выполняются в транзакции
запроса — внешний I/O в них опасен; dirty-флаг дешевле и надёжнее.

Порядок работы `sync_project_to_s3`:

1. Сервис `build_tasks_payload` / `build_rfis_payload` — SELECT всех заданий
   и RFI проекта (RFI — после Этапа C, до этого `items: []`).
2. PUT `tasks.json`, PUT `rfis.json`, затем `metadata.json` (последним — §3.1).
3. Транзакция: `SyncState.generation += 1`, `dirty = False`,
   `last_success_at = now`; запись `SyncLog(direction=out, level=info)`.
4. Ошибки S3: задача повторяется (`autoretry_for=(ClientError, BotoCoreError)`,
   `retry_backoff=True`, `max_retries=5`); при `consecutive_failures >= 3` —
   `SyncLog(level=error)` и уведомление ГИП через существующую подсистему
   Notification (как у напоминаний о заданиях).

#### 3.4.3. Dirty-сигналы

`storage/domain/signals.py`, к существующему `post_save(User)` добавить:

- `post_save(DesignTask)` и `post_delete(DesignTask)` → `mark_project_dirty(project_id)`
  — `update_or_create` `SyncState` с `dirty=True`; `dispatch_uid` против двойной
  регистрации. Ничего внешнего в сигнале не делаем (только UPDATE строки в БД).
- В Этапе C сюда добавляются `RfiTask`.

`mark_project_dirty` идемпотентен: повторные сигналы не создают дублей, а
задача выгрузки через 15 минут в любом случае снимет флаг после PUT.

### 3.5. Управляющие сигналы из S3 (пункт 3 задачи)

#### 3.5.1. Формат входящего файла

Ключ: `projects/{slug}/inbox/commands/{seq}_{type}_{cid}.json`, где `seq` — номер команды
(без ведущих нулей), `type` — тип из §3.5.2 (точки заменены на `.`, например `ack.import`),
`cid` — `command_id`. Перенос после обработки: `inbox/done/{seq}_{type}_{cid}.json`
или `inbox/failed/{seq}_{type}_{cid}.json`. Формат соответствует `storage/domain/sync/inbox.py:28-30`.

Пример тела команды:

```json
{
  "seq": 1,
  "command_id": "b19d...-uuid4",
  "ts": "2026-11-16T05:04:00Z",
  "type": "ack.import",
  "actor": "gip-assistant",
  "payload": {
    "acknowledged_generation": 42,
    "counts": {"tasks": 118, "rfis": 7},
    "errors": []
  }
}
```

Обработка идёт по возрастанию `seq` (разбор ключа из имени файла, §3.5.3); отправитель
обязан нумеровать команды монотонно в рамках проекта.

#### 3.5.2. Allowlist типов команд (MVP)

| Тип | Смысл | Сторона-инициатор | Обработка |
|---|---|---|---|
| `ack.import` | квитанция: ГИП-Ассистент импортировал generation N, ошибки | их импортер | автоматически: `SyncState.last_ack_generation`, `SyncLog(level=info)`; `errors` непустые → `level=warn` |
| `rfi.escalate` | их агент RFI эскалировал наш RFI (например, просрочен `rfi_due_date`) | их агент RFI | автоматически, если переход разрешён матрицей RFI-модуля (`sent`/`answered` → `escalated`); иначе `InboxCommand(status=failed)` с причиной в `error` |
| `rfi.proposal` | предложение создать RFI (аналог их `RfiProposal`: `formulation`, `rationale`, `rfi_due_date`) | их агент RFI | `InboxCommand(status=pending)`; ГИП подтверждает в UI проекта (вкладка RFI), после подтверждения — `create_rfi` сервиса RFI-модуля |
| `task.deadline_update` | ГИП скорректировал срок задания в своей модели | ГИП | `InboxCommand(status=pending)`; после подтверждения — `due_date` меняется только для открытых статусов (`ISSUED`/`IN_PROGRESS`/`ON_HOLD`/`REVIEW`), всё пишется в `SyncLog` |

Принцип: команды, меняющие данные, не применяются молча — создающие/правящие
команды требуют подтверждения ГИПа (соответствует их же правилу «без
доказательства система не действует автономно», их `DOMAIN.md` §2.6);
технические (`ack`) и «мягкие» (`rfi.escalate`, разрешённый переход) — автоматом.
Расширение allowlist — отдельная задача, согласуемая с командой ГИП-Ассистента.

#### 3.5.3. Алгоритм `process_s3_inbox` (на проект)

1. `list_keys("projects/{slug}/inbox/commands/")` → сортировка по `seq` (из имени ключа).
2. Для каждого файла:
   - скачать и распарсить JSON; невалидный → перенос в `failed/`, `SyncLog(error)`, к следующим;
   - валидация: `type` ∈ allowlist; `seq > SyncState.last_inbox_seq` (защита от
     повторной доставки старых файлов); `command_id` не встречается в
     `InboxCommand` (идемпотентность);
   - применить команду (§3.5.2) в транзакции: `InboxCommand(status=in_progress → done|failed)`
     с payload и ошибкой;
   - перенос файла: `move_object` (CopyObject → DeleteObject) в
     `inbox/done/` (или `inbox/failed/` после 3 неудачных попыток обработки).
3. Обновить `SyncState.last_inbox_seq = max(seq)`, `SyncLog(direction=in)`.

Poison-файл (кривой JSON) не блокирует очередь — обработка продолжается со
следующего файла. Потеря порядка при параллельных воркерах исключается:
задача обрабатывает проекты по одному, внутри проекта — строго последовательно
(для MVP достаточно; параллелизация на уровне проектов не вводится).

### 3.6. Настройки (пункт 4 задачи)

`sod_core/settings.py`, по существующей конвенции `os.environ.get` (как блок
`AI_EMBEDDING_*`, строки 160–177):

| Настройка | Env-переменная | Дефолт | Смысл |
|---|---|---|---|
| `S3_SYNC_ENABLED` | `S3_SYNC_ENABLED` | `False` | общий выключатель: без него beat-задачи выходят сразу, порт недоступен |
| `S3_ENDPOINT` | `S3_ENDPOINT` | `https://s3.cloud.ru` | endpoint Cloud.ru Evolution Object Storage |
| `S3_BUCKET_NAME` | `S3_BUCKET_NAME` | `''` | имя общего бакета |
| `S3_ACCESS_KEY` | `S3_ACCESS_KEY` | `''` | ключ доступа Stream-CAD (только outbox-write/inbox-read) |
| `S3_SECRET_KEY` | `S3_SECRET_KEY` | `''` | секретный ключ (только env, никогда не в git) |
| `S3_REGION` | `S3_REGION` | `''` | регион, если потребуется Cloud.ru (boto3 принимает пустой) |
| `S3_SYNC_SCHEMA_VERSION` | — (константа) | `'1.0'` | версия контракта, кладётся в envelope |

Дополнительно в boto3-клиенте (не env, константы адаптера): `connect_timeout=10`,
`read_timeout=30`, `retries={'max_attempts': 3}`.

Файлы для правки: `sod_core/settings.py`, `.env.example` и
`.env.production.example` — добавить блок «S3 Data Lake (ГИП-Ассистент)» с
`CHANGE_ME`-плейсхолдерами (так же, как сделано для SMTP/AI-блока).

Зависимости: `boto3` в `requirements.txt` (runtime). `django-storages` **не
добавляем** — S3 не становится бэкендом медиа-файлов Stream-CAD, это отдельный
интеграционный клиент. `moto` — в dev-зависимости для тестов (§4, шаг 9).

### 3.7. Безопасность (пункт 5 задачи)

#### 3.7.1. Разделение доступов

Два независимых IAM-пользователя (два комплекта ключей) на стороне Cloud.ru
Evolution Object Storage. Матрица прав (в нотации S3-действий):

| Префикс | Stream-CAD (наши ключи) | ГИП-Ассистент (их ключи) |
|---|---|---|
| `projects/*/outbox/*` | **PutObject** | GetObject, ListBucket |
| `projects/*/inbox/commands/*` | GetObject, ListBucket, DeleteObject (после обработки) | **PutObject** |
| `projects/*/inbox/done/*`, `projects/*/inbox/failed/*` | PutObject (перенос), GetObject, ListBucket | нет |
| корень бакета, `projects/*/` | ListBucket (только для разбора inbox) | ListBucket (только для разбора outbox) |
| прочие действия (DeleteBucket, PutBucketPolicy, ...) | нет | нет |

Ключевое следствие: **ни одна сторона не может испортить данные другой** —
ГИП-Ассистент физически не может переписать наш `outbox`, мы — их команды.
Политики настраиваются в консоли Cloud.ru в S3-совместимом виде (аналог
AWS policy-документа: `Effect/Action/Resource` на префиксы бакета); точный
синтаксис уточняется при настройке бакета — план фиксирует семантику прав,
а не кнопки консоли.

#### 3.7.2. Дополнительные меры

1. Секреты — только в env (`S3_SECRET_KEY`), в репозиторий не попадают;
   `.env.example` — с `CHANGE_ME`.
2. Валидация входящих: allowlist `type` (§3.5.2), лимит размера файла (1 МБ —
   команда больше не нужна никому), строгий JSON-парсинг, лимит `seq`.
3. Целостность: sha256 в манифесте (§3.3.5) — читатель ГИП-Ассистента может
   проверить, что файлы принадлежат одному поколению.
4. Шифрование: включить SSE (server-side encryption) на бакете.
5. Аудит: `SyncLog` + `InboxCommand` хранят всё, что пришло и ушло, включая
   payload команд; ретеншн 90 дней.
6. Ротация ключей — плановая операция (без простоя: boto3 читает env при
   создании клиента; перезапуск воркеров после ротации).
7. Минимум прав у приложения: ключи Stream-CAD живут только на сервере
   Stream-CAD; никогда не используются ни в чём другом.

### 3.8. Соотношение с external_api (Этап 14) и API Сводки

- **Data Lake — основной канал интеграции с ГИП-Ассистентом** (решение Координатора).
- `external_api` (`/api/v1/`, ApiToken) и `TaskDashboardApiView` (API Сводки)
  сохраняются: человекочитаемая Сводка для ГИПа, ручные проверки, отладка,
  резервный канал, пока импортер ГИП-Ассистента не выйдет на Data Lake.
- Расширение API Сводки до полного списка заданий **не планируется**: полный
  список теперь доставляет `tasks.json`; если ГИП-Ассистенту понадобится
  pull-канал — это отдельная задача.

---

## 4. Пошаговые изменения (файлы и функции)

Кодер реализует следующее (здесь — только перечень, не код):

### Шаг 1. Модели и миграция — `storage/domain/models.py`, `storage/migrations/0034_s3_sync_models.py` (факт; `0033` занята `0033_rfi_task_stage1`)

- `SyncState` (1:1 с `Project`): `dirty`, `enabled`, `generation` (int, default 0),
  `last_success_at`, `last_full_sync_at`, `last_error`, `consecutive_failures`,
  `last_inbox_seq`, `last_ack_generation`. Уникальность по проекту обеспечит
  `OneToOneField`.
- `SyncLog` (FK `Project`, null=True — чтобы логировать глобальные события):
  `direction` (`out`/`in`), `level` (`info`/`warn`/`error`), `generation`,
  `message`, `detail` (JSON), `created_at`. Индекс `(project, -created_at)`.
- `InboxCommand` (FK `Project`): `command_id` (UUID, **unique** — идемпотентность),
  `seq` (int), `type`, `status` (`pending`/`in_progress`/`done`/`failed` — фактические значения `InboxCommand.Status`, `models.py:1133-1137`),
  `payload` (JSON), `error` (Text, null), `file_key`, `received_at`, `processed_at`.
  Индекс `(project, status)` для очереди подтверждений.
- Регистрация в админке (read-only) — для аудита.

### Шаг 2. Порт и адаптер S3 — `storage/ports/s3_gateway.py`, `storage/adapters/s3_boto3.py`

- Порт: `put_json_object(key, data)`, `get_json_object(key)`, `list_keys(prefix)`,
  `move_object(src, dst)`, `delete_object(key)`, `object_exists(key)`.
- Адаптер: `boto3.client("s3", endpoint_url=settings.S3_ENDPOINT, ...)` лениво
  (при первом вызове), все методы проверяют `S3_SYNC_ENABLED` и поднимают
  понятное исключение, если выключено; ошибки boto3 не заворачиваются —
  Celery-ретраи смотрят на них напрямую.

### Шаг 3. Сериализация и экспорт — `storage/domain/sync/mapping.py`, `storage/domain/sync/export.py`

- `mapping.py` (чистые функции, легко тестируются): `uuid_for_project`,
  `uuid_for_design_task`, `uuid_for_rfi`; `map_task_status` (§3.3.2);
  `serialize_design_task(task) -> dict`; в Этапе C — `serialize_rfi_task(rfi) -> dict`.
- `export.py`: `build_tasks_payload(project) -> dict` (envelope + items),
  `build_rfis_payload(project) -> dict` (до Этапа C — `items: []`),
  `build_metadata(project, generation) -> dict` (справочник sections, counts,
  sha256 файлов), `export_project_to_s3(project) -> int` (фасад: собрать, положить
  в порядке §3.1, обновить SyncState/SyncLog). sha256 считается по байтам
  сериализованных файлов (ensure_ascii=False, separators — фиксируем в коде,
  чтобы чексуммы были воспроизводимы).

### Шаг 4. Входящие — `storage/domain/sync/inbox.py`

- `process_project_inbox(project) -> int` — алгоритм §3.5.3.
- `apply_command(project, command) -> bool` — allowlist §3.5.2; для
  `rfi.escalate` в Этапе D подключается матрица переходов RFI-модуля;
  до Этапа C типы `rfi.*` завершаются с `InboxCommand(status=failed)` и причиной в `error`
  («RFI-модуль не установлен»).

### Шаг 5. Celery и расписание — `storage/tasks.py`, `sod_core/settings.py`

- Четыре задачи по §3.4.2 (тонкие обёртки, как `notify_task_due_soon`).
- В `CELERY_BEAT_SCHEDULE` добавить 5 записей (15 мин / 5 мин / 02:30 / вс 03:00);
  у задач `autoretry_for`, `retry_backoff`, `max_retries=5`.

### Шаг 6. Dirty-сигналы — `storage/domain/signals.py`

- `post_save`/`post_delete` на `DesignTask` → `mark_project_dirty` (§3.4.3),
  `dispatch_uid`.

### Шаг 7. Настройки и зависимости — `sod_core/settings.py`, `.env.example`, `.env.production.example`, `requirements.txt`

- Блок настроек §3.6; `boto3` в requirements; комментарий в `.env.example`,
  кому принадлежат ключи и какие права у них.

### Шаг 8. Эксплуатация — `storage/management/commands/sync_s3.py`, админка

- `python manage.py sync_s3 --project <code> --direction out|in|status` —
  ручная выгрузка/разбор/просмотр состояния (для отладки с ГИП-Ассистентом).
- Админка: `SyncState`, `SyncLog`, `InboxCommand` (read-only).

### Шаг 9. Тесты — `storage/tests/test_s3_sync.py`

1. Юнит `mapping`: uuid5 стабильны и различны; маппинг всех 9 статусов;
   сериализация DesignTask даёт валидный против их схемы набор полей
   (`is_rfi=False`, `status` ∈ их enum, `section_code` заполнен).
2. Юнит `export`: envelope, counts, порядок `generation`, sha256.
3. Интеграция на `moto` (заглушка S3): round-trip «выгрузили → прочитали как
   их импортер»; dirty-сигнал после `DesignTask`-изменения; повторная выгрузка
   без изменений не увеличивает generation (только по dirty/full).
4. Интеграци inbox: valid `ack.import` применяется; дубликат `command_id`
   пропускается (идемпотентность); битый JSON уходит в `failed/` и не
   блокирует очередь; `seq` ниже `last_inbox_seq` игнорируется.
5. Проверки по конвенции проекта: `makemigrations --check`,
   отсутствие `RuntimeWarning` в тестах.

---

## 5. Зависимость от RFI-модуля (`RFI_MODULE_PLAN.md`)

`rfis.json` сериализует модель `RfiTask`, которую проектирует отдельный план
(RFI-модуль: модель с 6 состояниями, сервисы `create_rfi`/`send_rfi`/
`answer_rfi`/`close_rfi`/`escalate`, вкладка «Запросы информации (RFI)»,
подпапка «Запросы информации (RFI)» в ИРД). Этот план (s3-sync) фиксирует
**контрактную сторону**: обязательные поля `RfiTask`, попадающие в `rfis.json`:

- идентификация: `number` (РИ-xxx в рамках проекта), `title`, `description` (формулировка запроса);
- состояние: 6 значений, совпадающих с их `RfiState` (open/sent/answered/registered/closed/escalated);
- сроки: `rfi_due_date` (срок ответа Заказчика);
- документы: `request_document` / `response_document` → `Document` в подпапке
  «Запросы информации (RFI)» (для экспорта достаточно FK + метаданные);
- связи: `linked_tasks` (**NOT IMPLEMENTED**: модель не содержит связи с `DesignTask`; вариант A — поле убрать из контракта, вариант B — миграция M2M/FK, решает Координатор, §3.3.3);
- аудит: `sent_at`, `answered_at`, `registered_at`, `closed_at`, `escalated_at`.

Порядок раскатки это учитывает: Этап B (tasks.json) не ждёт RFI-модуля —
`rfis.json` с пустым `items[]` является валидным документом контракта.

---

## 6. Точки решения для Координатора

1. **Формат снапшотов.** Рекомендация: обычный JSON (§3.2). JSON Lines
   осознанно отклонён: S3 не умеет append, файл-за-команду атомарнее для
   inbox, а снапшоты по природе «целиком».
2. **Маппинг `ON_HOLD` → `NOT_STARTED`.** Альтернатива — `REWORK` (у них
   «требуется активная работа»), но семантически это разные состояния.
   Рекомендация: `NOT_STARTED` + `stream_cad_status` (§3.3.2).
3. **Выгрузка DRAFT-заданий.** Рекомендация: выгружать всё (полный список,
   требование Координатора); их матрица сама агрегирует. Если ГИП-Ассистент
   попросит исключить черновики — фильтр на этапе сериализации (одна строка).
4. **Подтверждение команд.** Рекомендация: создающие/правящие команды
   (`rfi.proposal`, `task.deadline_update`) — через `InboxCommand(status=pending)` с
   подтверждением ГИПа; `ack.import` и `rfi.escalate` — автоматически (§3.5.2).
5. **Стабильность `Project.code`.** Смена шифра меняет все uuid5 проекта.
   Рекомендация: при включённом s3-sync запретить смену шифра (проверка в
   сервисе переименования), либо смена шифра = новая папка в бакете + полная
   переимпорт-процедура у ГИП-Ассистента.
6. **Суффикс `-u{hash}` в slug.** Рекомендация: добавлять только когда шифр
   содержит символы вне `[A-Za-z0-9._-]` (типичный случай: `123-45.67`
   безопасен целиком).
7. **Версионирование бакета.** Рекомендация: включить.
8. **Зеркалирование файлов RFI в бакет.** MVP — только метаданные (§3.3.3);
   решение о выгрузке самих файлов — после приёмки Этапа B/C (этап F, §7).

---

## 7. Порядок раскатки

| Этап | Содержание | Зависимости | Результат |
|---|---|---|---|
| **A. Инфраструктура** | настройки, модели `0034`, порт/адаптер S3, `boto3`/`moto` | бакет в Cloud.ru, два IAM-пользователя, политики §3.7.1 | `sync_s3 --status` отвечает |
| **B. Выгрузка заданий** | `mapping` (DesignTask), `tasks.json` + `metadata.json`, Celery + beat, dirty-сигналы | A | ГИП-Ассистент впервые читает полный список заданий; `rfis.json` — валидный пустой |
| **C. RFI** | `serialize_rfi_task` (по `RFI_MODULE_PLAN.md`) | B + RFI-модуль | `rfis.json` полный, escalated виден |
| **D. Входящие** | inbox-процессор, allowlist §3.5.2, `ack.import`, `rfi.escalate`, `rfi.proposal`, `task.deadline_update` + подтверждения в UI | B (для RFI-команд — C) | двухсторонний контур закрыт: квитанции и команды работают |
| **E. Приёмка** | совместное тестирование с командой ГИП-Ассистента (их импортер против наших файлов), мониторинг `SyncLog`, документация (`ARCHITECTURE.md`, `HANDOFF.md`) | D | контракт подписан обеими сторонами |
| **F. (опция) Файлы RFI** | зеркалирование файлов «ИРД → RFI» в `outbox/documents/` | C + решение §6.8 | ГИП-Ассистент получает сами документы ответов |

Примечание: требование «3 неудачных попыток» (§3.5.3) реализуется на Шаге 4 (`inbox.py`, этап 4 backlog); текущее расхождение с кодом — в backlog (`docs/PROGRESS.md`).

---

## 8. Оценка объёма

| Часть | Объём |
|---|---|
| Модели + миграция | ~150 строк + миграция |
| Порт + адаптер S3 | ~120 строк |
| `mapping.py` + `export.py` | ~350 строк |
| `inbox.py` | ~200 строк |
| Celery + beat + сигналы | ~100 строк |
| Настройки/env/admin/команда | ~150 строк |
| Тесты | ~400–500 строк (15–20 кейсов, moto) |
| Итого | ~1500 строк у Кодера, 1 миграция, 2 новых зависимости (boto3, moto) |

Риски: (1) их импортер ещё не написан — контракт §3.3 согласовать с командой
ГИП-Ассистента до Этапа E; (2) XOR-ограничение их `ck_tasks_rfi_cause` при
импорте наших RFI — им потребуется обход (см. §3.3.3); (3) смена шифра
проекта — §6.5.

---

**Конец плана S3_SYNC_PLAN.md (проект, v1.0, 16.11.2026).**
