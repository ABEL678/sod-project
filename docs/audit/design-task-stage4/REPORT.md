# Отчёт аудита: Этап 4 — UI: карточка задания

**Дата:** 2026-11-15
**База патча:** `main`, HEAD `2e503d4` (Этап 3 применён и запушен)
**Спецификация:** `docs/DESIGN_TASKS_SPEC.md` v1.0, раздел 4.3; матрица
переходов — раздел 3 (реализована в сервисе `update_task_status`, Этап 2)
**Патч:** `design-task-stage4.patch` (применяется `git apply` на чистый `2e503d4`)

## 1. Что сделано

Карточка задания `/projects/<id>/tasks/<task_id>/` поверх сервисов Этапа 2.
Бизнес-логика не изменялась и не дублировалась: вьюхи тонкие, матрица
переходов, права и условия (обязательный комментарий, требование
output-вложения) целиком остаются в `update_task_status` /
`add_task_attachment` / `toggle_checklist_item`. Видимость кнопок в шаблоне —
только UI-подсказка по роли и текущему статусу; POST-запрос с подделанным
действием отклоняет сервис (покрыто тестами). Модели/миграции/CSS/JS не
менялись — только Bootstrap 5 и существующие классы (`text-bg-*`, `bi-*`,
`zone-*`-стиль вкладок, `cde-tnum`).

### 1.1. Карточка (GET `task_detail`, `TaskDetailView`)

| Блок спеки 4.3 | Реализация |
|---|---|
| Шапка | заголовок (h1), статус-бейдж — **те же классы `TASK_STATUS_BADGES`**, что во вкладке; приоритет (Высокий/Критический — жёлтый/красный); живой дедлайн-бейдж; строка «Задание #N · AR → KR · создано …» |
| «Суть» | `task.description` с сохранением переносов (`white-space: pre-line`, инлайн-стиль) |
| «Входные данные» | вложения `kind=input` с `select_related`; привязка к документу — ссылка на карточку документа (`storage:document_detail`), свободный файл — прямая ссылка; счётчик в шапке карточки |
| «Результат» | вложения `kind=output`, формат аналогичный; у исполнителя в статусе «В работе» — форма загрузки |
| «Чек-лист» | пункты с POST-кнопками-«чекбоксами» (обычная форма, без JS); выполненные — зачёркнуты с `done_by`/`done_at`; прогресс «N / M» |
| «История» | таймлайн `status_logs`: бейдж «old → new» (те же `TASK_STATUS_BADGES`), кто изменил, комментарий, время |
| Дедлайн | «осталось N дней» / «срок сегодня» / «просрочено на N дней»; классы Bootstrap: просрочка `text-bg-danger`, ≤3 дней `text-bg-warning`, остальное `text-bg-secondary`; у терминальных статусов (ACCEPTED/CANCELLED) бейджа нет |
| Правая колонка | «Реквизиты» (источник→цель, автор, исполнитель, срок, приоритет, шаблон, выдано/принято, закрыл) + пояснение, что права проверяются на сервере |

Плюрализация дней — через сервис `ru_plural` («осталось 1 день / 2 дня /
5 дней»).

### 1.2. Панель действий (по правам и статусу — матрица п. 3 спеки)

Кнопки собраны в правой колонке; для переходов «с комментарием» — форма с
обязательным textarea прямо в панели (POST `task_status`, поле `new_status`).
После успеха и после ошибки — redirect на карточку; ошибки сервиса
(`PermissionDenied`, `ValidationError`, включая «COMPLETED требует output» и
«RETURNED требует comment») показываются через `django.contrib.messages`
(рендерятся стандартным тост-механизмом проекта, как и flash Этапа 3).

| Переход | Кто видит кнопку |
|---|---|
| ISSUED / RETURNED → IN_PROGRESS | assignee — «Принять в работу» |
| IN_PROGRESS → ON_HOLD | assignee — «Приостановить» (textarea) |
| ON_HOLD → IN_PROGRESS | assignee — «Возобновить работу» |
| IN_PROGRESS → COMPLETED | assignee — «Выполнено»; без output-вложения карточка дополнительно показывает жёлтую подсказку, но проверку делает сервис |
| COMPLETED → REVIEW | author/GIP — «Отправить на проверку» |
| REVIEW → ACCEPTED | author/GIP — «Принять» |
| REVIEW → RETURNED | author/GIP — «Вернуть на доработку» (textarea) |
| любой не-терминальный → CANCELLED | author/GIP/admin — «Отменить» (textarea) |

Терминальные статусы (ACCEPTED, CANCELLED) — панели действий нет вовсе.

### 1.3. Вложения (POST `task_attachment`, `TaskAttachmentActionView`)

- **input** — форма у автора и ГИПа/админа (правило сервиса
  `add_task_attachment`; «только author» из промпта уточнено до правила
  сервиса, см. п. 6.3): опциональный select «Документ проекта» (сервис
  `get_readable_documents(user, project)`) и/или свободный файл;
- **output** — форма только у исполнителя и только в статусе «В работе»
  (права и статус проверяет сервис);
- вьюха дополнительно проверяет принадлежность выбранного документа
  проекту («Документ не найден в этом проекте»);
- имя свободного файла показывается без пути хранения (basename
  декорируется во вьюхе).

### 1.4. Чек-лист (POST `task_checklist`, `TaskChecklistActionView`)

- POST-форма на каждый пункт: кнопка-иконка `bi-square` / `bi-check2-square`
  (отмеченный — зелёный) + `item_id`;
- права (assignee или GIP/admin) проверяет `toggle_checklist_item`; у
  наблюдателя пункты видны с обычными иконками, без форм (тест
  `test_card_checklist_shown_not_toggleable_for_viewer`);
- отметка повторным POST снимается (сервис сбрасывает `done_by`/`done_at`);
- `item_id` чужого задания вьюха не находит в пунктах **этого** задания.

### 1.5. Интеграция со вкладкой «Задания»

Заголовок строки во вкладке стал ссылкой на карточку
(`{% url 'storage:task_detail' project.pk task.pk %}`) — единственное
изменение `project_detail.html`. URL карточки совпадает с
`_design_task_url` из сервисов (`/projects/<id>/tasks/<task_id>/`), т.е.
ссылки в уведомлениях Этапа 2 теперь ведут на живую страницу.

## 2. Изменённые файлы

| Файл | Тип | Что изменено |
|---|---|---|
| `storage/adapters/views.py` | изменён | +~300 строк: импорты (`DesignTaskAttachment`, `DesignTaskChecklistItem`, `DesignTaskStatusLog`, `add_task_attachment`, `toggle_checklist_item`, `update_task_status`, `get_readable_documents`), `TASK_DEADLINE_ACTIVE_STATUSES`, `_design_task_deadline`, миксин `_DesignTaskCardMixin`, `TaskDetailView`, `TaskStatusActionView`, `TaskAttachmentActionView`, `TaskChecklistActionView` |
| `storage/adapters/urls.py` | изменён | +4 маршрута: `task_detail`, `task_status`, `task_attachment`, `task_checklist` |
| `storage/adapters/templates/storage/project_detail.html` | изменён | +2 строки: заголовок строки — ссылка на карточку |
| `storage/adapters/templates/storage/task_detail.html` | новый | карточка: шапка с бейджами, 5 блоков спеки, панель действий, реквизиты; только существующие классы |
| `storage/tests/test_design_tasks_views.py` | изменён | +45 тестов в 4 классах (см. п. 4); импорты в шапке |
| `docs/audit/design-task-stage4/screenshots/*.png` | новые | 7 скриншотов UI (см. п. 5) |

Существующие модели, сервисы, миграции, admin, CSS и JS-файлы не
изменялись. `ARCHITECTURE.md` не трогался — модельных изменений нет
(правило владельца 4 касается изменений модели).

## 3. Проверки

### 3.1. `manage.py check`

```
System check identified no issues (0 silenced).
```

### 3.2. `manage.py test storage.tests.test_design_tasks_views`

```
Ran 75 tests in 111.0s

OK
```

(30 тестов Этапа 3 + 45 новых Этапа 4.)

### 3.3. Регресс

- `manage.py test storage issues` → **515 тестов, OK** (включая 28 сервисных
  тестов Этапа 2 и регресс 3c95380 для подрядчика —
  `test_card_contractor_sees_card`).

### 3.4. `manage.py makemigrations --check`

Как и на `3c95380`/чистом Этапе 1, проверка выдаёт 2 label-only AlterField
(`Document.suitability_code`, `Folder.zone`) — **предсуществующий дрейф**,
присутствующий на чистом `2e503d4` без патча (задокументирован в отчёте
Этапа 1, п. 5.1; рекомендация отдельного label-only `0032` в силе).
Этап 4 миграций не добавляет и модификаций моделей не делает.

## 4. Список тестов (45 новых)

**TaskDetailViewTests (16):** test_card_requires_login,
test_card_no_project_access (чужак → 404),
test_task_of_other_project_404 (задание другого проекта не открывается
через URL этого проекта), test_card_shows_all_blocks (шапка/Суть/разделы/
бейдж `text-bg-info`), test_card_history_displayed (3 перехода, бейджи,
комментарий), test_card_history_empty_for_draft, test_card_deadline_labels
(«просрочено на 2 дня»/danger, «осталось 10 дней»/secondary, «срок
сегодня»/warning), test_card_deadline_hidden_for_accepted,
test_card_links_from_tasks_tab (заголовок-ссылка во вкладке),
test_card_action_buttons_by_role (assignee видит «Принять в работу»,
author — «Отменить»), test_card_review_buttons_for_author_and_gip,
test_card_complete_hint_without_output (подсказка «приложите результат»),
test_card_attachment_forms_by_role (input — автору, output — исполнителю),
test_card_attachment_document_link (ссылка на документ проекта),
test_card_checklist_shown_not_toggleable_for_viewer,
test_card_contractor_sees_card (регресс 3c95380).

**TaskStatusActionViewTests (15):** test_start_work_by_assignee (redirect на
карточку, лог), test_start_work_denied_for_author («недоступен вашей
роли»), test_hold_requires_comment, test_hold_with_comment_ok,
test_resume_from_hold, test_complete_without_output_rejected (статус не
меняется, сообщение сервиса), test_complete_with_output_ok,
test_accept_by_gip (accepted_at), test_return_requires_comment,
test_return_with_comment_notifies_assignee (Notification),
test_cancel_by_author_with_comment, test_cancel_accepted_rejected
(терминальный статус), test_unknown_status_rejected,
test_status_action_no_project_access, test_status_action_requires_post (405).

**TaskAttachmentActionViewTests (8):** test_output_upload_by_assignee
(kind/output, uploaded_by), test_output_upload_denied_for_author,
test_output_upload_wrong_status (сервис: только в «В работе»),
test_input_upload_with_document (document FK),
test_input_upload_foreign_document_rejected (вьюха: документ другого
проекта), test_attachment_requires_file_or_document,
test_attachment_no_project_access (404), test_attachment_requires_post (405).

**TaskChecklistActionViewTests (6):** test_toggle_by_assignee (отметка +
повторное снятие, done_by/done_at), test_toggle_by_gip_ok,
test_toggle_denied_for_viewer (сервис отклоняет, пункт не меняется),
test_unknown_item_rejected, test_item_of_other_task_rejected
(item_id чужого задания), test_checklist_action_requires_post (405).

## 5. Описание UI (скриншоты в `docs/audit/design-task-stage4/screenshots/`)

| Файл | Что показывает |
|---|---|
| `01_task_card_in_progress_assignee_full.png` | Карточка «В работе» у исполнителя (полная страница): бейджи статуса/приоритета/дедлайна, Суть, Входные данные (документ AR-01), Результат (файл), Чек-лист 2/3 с зачёркнутыми пунктами, История с бейджами переходов и комментарием паузы, панель действий (Приостановить с textarea, Выполнено), Реквизиты |
| `02_task_card_in_progress_assignee_top.png` | Верх той же карточки крупно: шапка с тремя бейджами («В работе», «Критический», «просрочено на 2 дня» красный), строка «Задание #3 · AR → KR», панель действий и Реквизиты |
| `03_service_error_flash.png` | POST «Выполнено» без output: карточка остаётся в «В работе», сервисная ошибка показана пользователю (тост «Перед отметкой выполнения приложите результат (output)») + жёлтая подсказка в панели |
| `04_task_card_review_gip_full.png` | Карточка «На проверке» у ГИПа: кнопки «Принять», «Вернуть на доработку» (с причиной), «Отменить» (с причиной); input-форма ГИПа; живой дедлайн «осталось 3 дня» |
| `05_task_card_accepted_history_full.png` | Карточка «Принято»: нет панели действий (терминальный статус) и нет дедлайн-бейджа; полная история 9 переходов, включая возврат с комментарием; Реквизиты «Принято/Закрыл» |
| `06_tasks_tab_links_to_cards.png` | Вкладка «Задания»: заголовки строк — ссылки на карточки, статусы/приоритеты/просрочка как прежде |
| `07_task_card_draft_author_full.png` | Карточка черновика: «История пуста — задание ещё не выдавалось (черновик)»; у пользователя без прав на это задание панели действий нет |

Скриншоты сняты в браузере (Chromium 1440×900) на живом dev-сервере с
демо-данными проекта `DT-UI` (сценарий в `scripts/screenshot_scenario*.py`,
в репозиторий не входит).

## 6. Интерпретации и замечания аудита

### 6.1. Живой дедлайн: аналогом `issue_extras`, не reuse

`issue_extras.deadline_label/deadline_class` привязан к статусам **Issue**
(`ACTIVE_STATUSES = {NEW, IN_PROGRESS, DISCUSSION, REJECTED}`): с
`DesignTask.Status` пересекается только «IN_PROGRESS», т.е. бейдж
показывался бы исключительно в работе — «по смыслу не подходит». По
разрешению промпта добавлен аналог `_design_task_deadline` во views.py
(адаптерный слой, UI-функция): те же тексты, но классы — Bootstrap
(`text-bg-danger/warning/secondary`), без нового CSS; активные статусы —
все не-терминальные (тот же принцип, что в фильтрах вкладки и
`task_summary_for_gip`). Плюрализация — сервис `ru_plural`, т.е. текст не
дублируется.

### 6.2. Кнопки — подсказки, матрица — сервис

Видимость кнопок построена на роли и текущем статусе, но **не** на
копии матрицы: она не проверяет допустимость перехода, а лишь скрывает
кнопки, которые сервис в текущем статусе всё равно отклонит. POST-запрос
с подделанным `new_status` проходит через `update_task_status` и получает
ошибку сервиса (тесты `test_start_work_denied_for_author`,
`test_unknown_status_rejected`, `test_cancel_accepted_rejected`). Предикат
«ГИП/админ» — аддитивный сервис Этапа 3 `can_view_all_tasks`, тот же, что
внутри PermissionDenied-гейтов сервисов: UI и права не расходятся.

### 6.3. input-вложение: автор **или ГИП/админ**

Промпт формулирует «input (только author)», но сервис Этапа 2 разрешает
input автору **или** ГИПу/админу («Входные данные добавляет автор
задания, ГИП или админ»). Так как «все проверки — через сервисы», форма
input показана тем же кругу, что допускает сервис (author OR gip/admin);
в остальных случаях POST отклоняется сервисом. Расхождение зафиксировано
здесь — при необходимости сузить права правится сервис Этапа 2.

### 6.4. Обычные HTML-формы, без HTMX и JS

По прецеденту Этапа 3 и ограничению «новые JS-файлы не создавать» все
действия карточки — обычные POST-формы с CSRF; чекбокс чек-листа —
submit-кнопка с иконкой. Комментарий для ON_HOLD/RETURNED/CANCELLED —
textarea прямо в панели действий (не модалка): видно, что поле
обязательное, и работает без JS. Flash-сообщения рендерятся существующим
механизмом проекта (тосты) — новых CSS/JS нет.

### 6.5. Прочие решения

- **URL**: `/projects/<id>/tasks/<task_id>/` — ровно как `_design_task_url`
  в сервисах (уведомления Этапа 2 теперь ведут на карточку);
  `task_status`/`task_attachment`/`task_checklist` — подмаршруты карточки.
- **404 вместо 403**: чужой проект/задание — `get_object_or_404` по
  `get_accessible_projects` (тот же паттерн, что во вкладке и форме);
  конкретные права действий проверяют сервисы.
- **Декор строк** (подписи/бейджи статусов лога, basename файлов) — во
  вьюхе, шаблон не собирает их сам; словари choices не попадают в шаблон.
- **Пагинация истории/вложений** не предусмотрена спекой; объём записи
  лога ограничен числом переходов.
- **ARCHITECTURE.md** не обновлялся: новых моделей/сервисов нет, UI-слой
  описан в отчёте (правило владельца 4 — про модельные изменения).
