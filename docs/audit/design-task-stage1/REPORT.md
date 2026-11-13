# Отчёт аудита: Этап 1 — Модели и миграции модуля «Задания» (DesignTask)

**Дата:** 2026-11-13
**База патча:** `main`, HEAD `c717b97c3dcc7624d176b0f87352b6f7e0e1bcc9`
**Спецификация:** `docs/DESIGN_TASKS_SPEC.md` v1.0 (прочитана до начала работ)
**Патч:** `design-task-stage1.patch` (применяется `git apply` на чистый `c717b97`)

## 1. Что сделано

Созданы 5 моделей модуля «Задания» (спека 2.1–2.5) в `storage/domain/models.py`
(рядом с `Document`), одна новая миграция, обновлён `ARCHITECTURE.md`
(правило владельца №4: «Модели менять только с обновлением ARCHITECTURE.md»).
Новых файлов моделей не создавалось, существующие модели (`Document`, `Issue`,
`Project`, `Section`, `Company` и др.) не менялись.

| Модель | Таблица | Ключевые требования спеки |
|---|---|---|
| `DesignTask` | `storage_designtask` | status 9 значений (DRAFT…CANCELLED), priority low/normal/high/critical, `external_id` CharField(100) null+db_index (ГИП-Ассистент, спека 7), индекс `(project, source_section, target_section, title, created_at)` |
| `DesignTaskStatusLog` | `storage_designtaskstatuslog` | `task.status_logs`, old/new_status, changed_by PROTECT, comment blank |
| `DesignTaskAttachment` | `storage_designtaskattachment` | kind input/output/comment, `document.design_task_attachments` (спека 7) |
| `TaskTemplate` | `storage_tasktemplate` | `checklist` JSONField default=list, уникальность `(company, title)` |
| `DesignTaskChecklistItem` | `storage_designtaskchecklistitem` | БЕЗ уникальности — дубликаты пунктов разрешены |

## 2. Изменённые файлы

| Файл | Тип | Что изменено |
|---|---|---|
| `storage/domain/models.py` | изменён | +5 моделей (~325 строк, блок «Модуль „Задания“») |
| `storage/migrations/0031_design_task_stage1.py` | новый | 5×CreateModel + AddField(template) + AddConstraint + AddIndex |
| `storage/admin.py` | изменён | импорт + 5 админ-классов (list_display, list_filter, search_fields) |
| `docs/ARCHITECTURE.md` | изменён | раздел 3.2: 5 блоков моделей; раздел 3.3: связи |

## 3. Проверки

### 3.1. `manage.py check`

```
System check identified no issues (0 silenced).
```

### 3.2. `manage.py makemigrations storage --check --dry-run`

По моделям Этапа 1 — «No changes detected» (миграция 0031 покрывает все 5
моделей полностью). Команда остаётся с двумя операциями, которые существуют
на чистом `c717b97` ДО этого патча (см. п. 5.1):

```
storage/migrations/0032_alter_document_suitability_code_alter_folder_zone.py
    ~ Alter field suitability_code on document
    ~ Alter field zone on folder
```

**Проверено на чистом c717b97 (без патча):** те же две операции —
предсуществующий дрейф, не привнесённый Этапом 1.

### 3.3. Миграция применена

`manage.py migrate` (sqlite) — все приложения, включая
`0031_design_task_stage1... OK`. `sqlmigrate storage 0031` содержит только
`CREATE TABLE` (5 новых таблиц) и `CREATE INDEX` (FK-индексы, FK-констрейнты,
`design_task_proj_src_tgt_idx`, уникальность шаблона). ALTER существующих
таблиц отсутствует.

### 3.4. Смоук-тест поведения моделей (22/22 PASS)

Дубликат `(company, title)` шаблона → IntegrityError; два одинаковых пункта
чек-листа → разрешены; `author` — обычный пользователь (не ГИП) → OK;
`external_id` сохраняется; PROTECT на оба раздела (ProtectedError при удалении
Section с заданиями); `status_logs`/`attachments`/`checklist_items` работают;
все 5 моделей зарегистрированы в админке; choices в точности по спеке.

## 4. Проверка «по смыслу» (для Координатора)

1. **Существующие связи не сломаны.** Миграция содержит только CreateModel по
   5 новым таблицам (FK на существующие `Project`/`Section`/`User`/`Document`/
   `Company`); sqlmigrate подтверждает отсутствие ALTER. `Document`, `Issue`,
   `Project`, `Section`, `Company` в models.py и в прошлых миграциях не тронуты.
2. **`external_id` есть.** `CharField(100), null=True, db_index=True` (+blank=True
   для форм — см. п. 5.3) — готов к синхронизации с ГИП-Ассистентом (спека 7).
3. **`author` не обязательно ГИП.** `FK → User, PROTECT`, без ограничения по
   роли; write-доступ к `source_section` проверяется сервисом `create_task`
   (Этап 2) через существующий `check_write_permission` — на уровне модели
   ограничений нет, что и требуется.
4. **Миграции — только новые таблицы.** Да (см. п. 3.3); зависимости —
   `('storage', '0030_documentversion_indexing_status')` + AUTH_USER_MODEL.

## 5. Расхождения со спекой и замечания аудита

### 5.1. Предсуществующий дрейф миграций на c717b97 (не входит в патч)

`makemigrations --check --dry-run` на чистом `c717b97` НЕ даёт «No changes
detected»: требует `AlterField` для `Document.suitability_code` (миграция 0018
хранит лейблы «S0 — Исходный (WIP)», модель — «Черновик») и `Folder.zone`
(0010 — «В работе», модель — «В разработке»). Комментарий в models.py ошибочно
считает, что «миграция не требуется» — лейблы choices являются частью
состояния миграций. Обе операции меняют только лейблы (значения БД те же) и
SQL не порождают. В патч Этапа 1 их включать нельзя (спека 8: «существующие
не трогаются»). **Рекомендация:** отдельным патчем добавить label-only
миграцию `0032` (2×AlterField, без SQL), после чего `makemigrations --check`
станет чистым; либо зафиксировать исключение в HANDOFF.md.

### 5.2. on_delete, не заданные в таблицах спеки (интерпретация)

Спека задаёт PROTECT только для `source_section`/`target_section`/`author`/
`uploaded_by`/`changed_by`. Для nullable-FK выбран `SET_NULL` (единственно
возможный при null=True): `assignee`, `closed_by`, `template`, `done_by`,
`attachment.document`, `template.source_section/target_section`.
`attachment.document → SET_NULL` — осознанно: жёсткое удаление документа
(вместе с проектом) не должно стирать вложение и файл — теряется только связь.

### 5.3. Мелкие дополнения сверх таблиц спеки (не конфликтуют)

- `external_id`: добавлен `blank=True` (для админки/форм; как у
  `Issue.external_id`).
- Добавлены значения по умолчанию: `status=DRAFT`, `priority=normal`
  (соответствуют сигнатуре `create_task` и потоку спеки 3).
- `TaskTemplate.source/target_section` получили `blank=True` (подсказки,
  не обязательные поля).

## 6. Применение

```bash
git checkout main && git pull            # ожидается HEAD c717b97 (или позже)
git apply --check design-task-stage1.patch
git apply design-task-stage1.patch
python manage.py check
python manage.py migrate storage
python manage.py makemigrations storage --check --dry-run
# см. п. 3.2: останутся 2 label-only AlterField — предсуществующий дрейф (п. 5.1)
```
