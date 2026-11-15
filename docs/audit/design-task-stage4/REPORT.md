# Отчёт аудита: Этап 4 — UI: карточка задания (раунд 2, проверка d94e09f)

**Дата:** 2026-11-15
**Роль:** Аудитор (Z.ai, режим Debug) — проверка реализации по критериям
приёмки Координатора
**База:** `d94e09f` `feat(tasks): stage 4 task card z.ai` (HEAD `origin/main`,
Этап 4 применён и запушен)
**Спецификация:** `docs/DESIGN_TASKS_SPEC.md` v1.0 — п. 4.3 (карточка),
п. 3 (матрица переходов), п. 7 (интеграция «Документы»)
**Патч аудита:** `design-task-stage4-audit.patch` — применяется `git apply`
на чистый `d94e09f`; содержит **только views/шаблоны/тесты/скриншоты**,
`services.py`, модели и миграции не тронуты
**Патч реализации:** `design-task-stage4.patch` (раунд 1, база `2e503d4`)

## 1. Резюме

Реализация карточки на `d94e09f` соответствует спеке 4.3 по структуре:
4 вьюхи (`TaskDetailView`, `TaskStatusActionView`, `TaskAttachmentActionView`,
`TaskChecklistActionView`), матрица переходов не дублируется во вьюхах
(все POST — через сервисы Этапа 2), 6 блоков карточки на месте, панель
действий собрана по ролям и статусу, CSRF/логин/404-гейды работают.

По критериям приёмки Координатора зафиксировано **5 пунктов REWORK** —
все исправлены в `design-task-stage4-audit.patch`:

| # | REWORK | Исправление |
|---|---|---|
| R1 | Нет HTMX-поллинга живого дедлайна | Слот `#taskDeadlineSlot` с `hx-get ?partial=deadline`, `hx-trigger="every 60s"` (TTL 60 с), `hx-swap="outerHTML"`; фрагмент отдаёт `TaskDetailView` |
| R2 | Чек-лист — обычный POST с перезагрузкой, не HTMX toggle | Каждый пункт — `hx-post` форма; ответ — фрагмент `_task_checklist.html` (`outerHTML`), счётчик и отметка обновляются без перезагрузки; без HTMX работает прежний POST-redirect (прогрессивное улучшение) |
| R3 | Нет блока «Связанные задания» в `document_detail.html` (спека п. 7) | Контекст `related_tasks` в `DocumentDetailView` (один запрос, `select_related`, `distinct`; анониму по share-ссылке — пусто) + блок в панели документа с бейджами `TASK_STATUS_BADGES` и ссылками на карточки |
| R4 | Загрузка input/output без индикатора прогресса и без ограничения типов файлов | `hx-indicator` → Bootstrap-спиннер на время запроса; белый список `ALLOWED_UPLOAD_EXTENSIONS` + лимит `MAX_UPLOAD_SIZE` во вьюхе (те же константы, что у версий документов) |
| R5 | `kind` не ограничен во вьюхе (POST `kind=comment` прошёл бы сервис) | Вьюха допускает только `input`/`output` — «Недопустимый тип вложения для карточки задания» |

**Вердикт: READY FOR APPROVE** после применения
`design-task-stage4-audit.patch`. Коммит не выполняется до APPROVE
Координатора.

## 2. Что проверено (перечень файлов)

| Файл | Что проверено | Вердикт |
|---|---|---|
| `storage/adapters/urls.py` | 4 маршрута `task_detail`/`task_status`/`task_attachment`/`task_checklist` под `/projects/<id>/tasks/<task_id>/` | OK |
| `storage/adapters/views.py` | Вьюхи карточки: тонкость (только вызов сервисов), поиск объектов через `get_accessible_projects` + `get_object_or_404` (чужой проект/задание → 404), флаги видимости как UI-подсказка, HTMX-фрагменты, валидация файлов/kind | OK (+R1–R5) |
| `storage/adapters/templates/storage/task_detail.html` | Шапка, 5 блоков, панель действий, реквизиты; только Bootstrap/bi-*; CSRF во всех формах | OK (+R1/R2) |
| `storage/adapters/templates/storage/project_detail.html` | Заголовок строки вкладки — ссылка на карточку | OK |
| `storage/adapters/templates/storage/_task_*.html` | Новые фрагменты: `_task_deadline.html`, `_task_input_card.html`, `_task_output_card.html`, `_task_checklist.html` — один и тот же DOM используется на странице и в HTMX-ответе | новые (R1–R4) |
| `storage/adapters/templates/storage/document_detail.html` | Блок «Связанные задания» внутри `aside` замечаний (вне `#list` — JS фильтров замечаний не затронут), скрыт у `public_view` | исправлено (R3) |
| `storage/domain/services.py` | **Не изменён** (контроль: `git diff d94e09f -- storage/domain/services.py` пуст); контракт `update_task_status`/`add_task_attachment`/`toggle_checklist_item` соответствует вызовам вьюх | OK |
| Модели/миграции/admin/CSS/JS | Не изменены; `manage.py check` — 0 issues | OK |
| `storage/tests/test_design_tasks_views.py` | 87 тестов (было 75), +12 на REWORK-функциональность | OK |

## 3. Критерии приёмки Координатора — по пунктам

| # | Критерий | Статус | Комментарий |
|---|---|---|---|
| 1 | URL и вьюхи; матрица не дублируется | **OK** | POST-действия — только вызов `update_task_status`; флаги `can_*` — подсказка видимости, валидность перехода гарантирует сервис (подделанный POST отклоняется: `test_start_work_denied_for_author`, `test_unknown_status_rejected`, `test_cancel_accepted_rejected`) |
| 2 | Шапка: заголовок/бейдж/приоритет/живой дедлайн + HTMX-поллинг | **OK** (поллинг: R1→исправлено) | Бейджи — `TASK_STATUS_BADGES`; просрочка красная с иконкой `bi-alarm-fill`; поллинг 60 с |
| 3 | Блоки: Суть/Входные/Результат/Чек-лист (HTMX toggle)/История | **OK** (R2→исправлено) | Входные данные с привязкой к документам (ссылка `document_detail`); история — таймлайн `status_logs` с бейджами old→new, кто, комментарий, время |
| 4 | Панель действий по правам и статусу | **OK** | Все 9 переходов матрицы, включая COMPLETED→REVIEW и «Любой→CANCELLED» (не-терминальный); «Выполнено» с подсказкой про output, валидацию делает сервис |
| 5 | Права: assignee/author/gip; сервисный слой | **OK** | Toggle чек-листа — assignee/ГИП/админ (сервис); у наблюдателя пункты без форм; input — автор/ГИП, output — исполнитель в «В работе»; история видима участникам проекта (доступ к карточке ограничен `get_accessible_projects`) |
| 6 | UI/UX: Bootstrap 5 + HTMX, без нового CSS; бейджи; просрочка красная + иконка; чек-лист мгновенно; загрузка с прогрессом и валидацией kind | **REWORK → исправлено** | R1/R2/R4/R5; новых CSS/JS-файлов нет — только `hx-*`-атрибуты, Bootstrap-спиннер и классы `htmx-indicator` самой библиотеки HTMX |
| 7 | Интеграция: «Связанные задания» в `document_detail.html` | **REWORK → исправлено** | R3; блок по `DesignTaskAttachment.document`, ссылки на карточки, `public_view` — пусто |
| 8 | Тесты `storage.tests.test_design_tasks_views` 45/45, `--noinput`; регресс без падений | **OK** | Модуль: **87/87** (раунд 1: 75; раунд аудита: +12); регресс `storage.tests issues`: **527/527**; все запуски с `--noinput` |
| 9 | Безопасность: HTMX-эндпоинты защищены, CSRF, валидация kind, ограничение типов файлов, проверка существования task/project | **OK** (R4/R5→исправлено) | Все HTMX-эндпоинты — те же LoginRequiredMixin-вьюхи с проверкой проекта (404); CSRF-токен внутри каждой hx-post-формы (htmx сериализует поля формы); `?partial=deadline` закрыт тем же гейдом (`test_card_deadline_polling_partial_requires_project_access`) |
| 10 | Производительность: N+1 нет, поллинг с разумным TTL | **OK** | Карточка: `select_related` (задание, вложения, лог) + 2 запроса списков; «Связанные задания» — 1 запрос с `select_related`/`distinct`; поллинг — 60 с, фрагмент = 1 точечный запрос по PK |

## 4. REWORK-пункты — детально

### R1. HTMX-поллинг живого дедлайна

- `task_detail.html`: в шапке `{% include 'storage/_task_deadline.html' %}`;
- `_task_deadline.html`: слот `<span id="taskDeadlineSlot">` с
  `hx-get="…/tasks/<id>/?partial=deadline"` `hx-trigger="every 60s"`
  `hx-swap="outerHTML"` — при смене суток бейдж «осталось N дней» /
  «просрочено» обновляется сам;
- `TaskDetailView.get`: ветка `?partial=deadline` возвращает только слот;
  доступ — тот же `LoginRequiredMixin` + `_get_task` (чужой проект → 404);
- TTL 60 с выбран как компромисс (заметное обновление «срока сегодня»
  без нагрузки); запрос — PK-точечный.
- Просрочка: `text-bg-danger` + `bi-alarm-fill` (флаг `overdue` в
  `_design_task_deadline`); прочие — `bi-calendar-event`.

### R2. Чек-лист — HTMX toggle (мгновенное сохранение)

- Каждая кнопка-«чекбокс» — форма с `hx-post` на `task_checklist`,
  `hx-target="#taskChecklistCard"`, `hx-swap="outerHTML"`;
- `TaskChecklistActionView` при `request.htmx` отдаёт фрагмент
  `_task_checklist.html` с свежими пунктами, счётчиком «N / M» и
  сообщением («Пункт «…» отмечен.» / ошибка сервиса) прямо в карточке;
- права по-прежнему проверяет `toggle_checklist_item` — у наблюдателя
  HTMX-ответ содержит ошибку и пункт не меняется
  (`test_toggle_htmx_denied_for_viewer`);
- без HTMX (JS отключён) — прежний POST + redirect + messages.

### R3. «Связанные задания» в карточке документа

- `DocumentDetailView.get_context_data`: `related_tasks` =
  `DesignTask.objects.filter(attachments__document=document)`
  `.distinct().select_related('project', 'assignee')` + бейдж
  `TASK_STATUS_BADGES`; у `public_view` — пустой список (аноним не
  получает тексты заданий, как и замечания);
- `document_detail.html`: блок внутри панели замечаний (после `#list`,
  до `#detail` — JS фильтров замечаний не затронут): заголовок
  «Связанные задания N» + карточки «#N <title>» со статус-бейджем,
  исполнителем и сроком; ссылка ведёт на карточку задания;
- расхождение прав закрыто само: доступ к документу уже означает
  доступ к проекту, карточка задания открыта всем участникам проекта
  (как и по прямой ссылке).

### R4. Загрузка input/output — индикатор и ограничение типов файлов

- Формы вложений — `hx-post` с `hx-target` на свою карточку
  (`#taskInputCard` / `#taskOutputCard`) и `hx-indicator` →
  `<span class="spinner-border spinner-border-sm htmx-indicator">`
  на кнопке: на время запроса виден Bootstrap-спиннер (прогресс
  операции; построчный прогресс байтов потребовал бы JS-файла —
  вне ограничений Этапа);
- во вьюхе — та же политика, что у версий документов: расширение из
  `ALLOWED_UPLOAD_EXTENSIONS` (pdf/dwg/dxf/office/images/archives/…),
  не пустой файл, ≤ `MAX_UPLOAD_SIZE` (100 МБ); ошибки — текстом
  во фрагменте или flash («Формат «exe» не поддерживается…»);
- HTMX-ответ — обновлённый фрагмент карточки со списком и сообщением
  («Результат: вложение добавлено.»); без HTMX — прежний redirect.

### R5. Валидация kind во вьюхе

- Карточка — форма вложения, а не комментарии: POST `kind=comment`
  отклоняется до сервиса («Недопустимый тип вложения для карточки
  задания»), сервис по-прежнему допускает comment как вид вложения
  для API/админки — правки services.py не требуются.

## 5. Матрица прав (сводка по карточке)

| Действие | assignee | author | ГИП/админ | viewer/прочие | Где проверяется |
|---|---|---|---|---|---|
| Просмотр карточки, истории, чек-листа | ✓ | ✓ | ✓ | ✓ (участники проекта) | `get_accessible_projects` (404 чужим) |
| «Принять в работу» (ISSUED/RETURNED→IN_PROGRESS) | ✓ | — | — | — | сервис + видимость кнопки |
| «Приостановить» (IN_PROGRESS→ON_HOLD, комментарий) | ✓ | — | — | — | сервис (комментарий обязателен) |
| «Возобновить» (ON_HOLD→IN_PROGRESS) | ✓ | — | — | — | сервис |
| «Выполнено» (IN_PROGRESS→COMPLETED) | ✓ (при наличии output) | — | — | — | сервис (output проверяет он же) |
| «Отправить на проверку» (COMPLETED→REVIEW) | — | ✓ | ✓ | — | сервис |
| «Принять» (REVIEW→ACCEPTED) | — | ✓ | ✓ | — | сервис |
| «Вернуть на доработку» (REVIEW→RETURNED, комментарий) | — | ✓ | ✓ | — | сервис |
| «Отменить» (не-терминальный→CANCELLED, комментарий) | — | ✓ | ✓ | — | сервис |
| Вложение input | — | ✓ | ✓ | — | сервис |
| Вложение output | ✓ (в «В работе») | — | — | — | сервис |
| Toggle чек-листа | ✓ | — | ✓ | — | сервис |
| Загрузка через карточку: kind input/output | ✓/✓ | ✓/— | ✓/— | — | вьюха (R5) + сервис |

Подделанный POST (чужая роль/статус/несуществующий item) отклоняется
сервисом/вьюхой — покрыто тестами
(`test_start_work_denied_for_author`, `test_output_upload_denied_for_author`,
`test_output_upload_wrong_status`, `test_toggle_denied_for_viewer`,
`test_item_of_other_task_rejected`, `test_unknown_status_rejected`,
`test_cancel_accepted_rejected`, `test_attachment_kind_comment_rejected_from_card`).

## 6. Чек-лист UI/UX

| Пункт | Статус |
|---|---|
| Только Bootstrap 5 + HTMX, без новых CSS/JS-файлов | ✓ (hx-атрибуты, `htmx-indicator` — класс самой HTMX) |
| Бейджи статусов — `TASK_STATUS_BADGES` (шапка, история, «Связанные задания») | ✓ |
| Просроченный срок — красный + иконка | ✓ (`text-bg-danger` + `bi-alarm-fill`) |
| Живой дедлайн с поллингом (TTL 60 с) | ✓ (R1) |
| Чек-лист — мгновенное сохранение (HTMX toggle, счётчик N/M) | ✓ (R2) |
| Загрузка input/output — индикатор (спиннер) + валидация kind + белый список форматов + лимит размера | ✓ (R4/R5) |
| Ошибки сервиса видны пользователю (flash или фрагмент) | ✓ |
| CSRF во всех POST-формах (обычных и hx-post) | ✓ |
| Иконки bi-*, aria-label на иконочных кнопках, visually-hidden-тексты | ✓ |
| Ссылки: вкладка→карточка, карточка→документ, документ→карточка | ✓ |
| Терминальные статусы — без панели действий и без дедлайн-бейджа | ✓ |

## 7. Тесты

### 7.1. Рабочая копия (патч применён)

```
manage.py test storage.tests.test_design_tasks_views --noinput
Ran 87 tests in 129.4s — OK

manage.py test storage.tests issues --noinput
Ran 527 tests in 516.1s — OK

manage.py check
System check identified no issues (0 silenced).
```

### 7.2. Чистый клон `d94e09f` + `design-task-stage4-audit.patch`

```
git apply --check — OK; git apply — OK
manage.py check — 0 issues
manage.py test storage.tests.test_design_tasks_views --noinput
Ran 87 tests in 130.6s — OK
```

### 7.3. Новые тесты раунда аудита (+12)

**TaskDetailViewTests (+5):** test_card_deadline_polling_partial
(слот, `every 60s`, красный бейдж, `bi-alarm-fill`, «просрочено на 2 дня»),
test_card_deadline_polling_partial_requires_project_access (404 чужому),
test_card_deadline_polling_partial_without_deadline (пустой слот без срока),
test_document_detail_related_tasks (блок, ссылка на карточку),
test_document_detail_without_related_tasks (блока нет).

**TaskAttachmentActionViewTests (+5):** test_output_upload_htmx_fragment
(200-фрагмент вместо redirect, вложение в списке, сообщение),
test_attachment_file_type_restricted (`.exe` → «не поддерживается»),
test_attachment_file_type_restricted_htmx (то же во фрагменте),
test_attachment_file_size_restricted (mock `MAX_UPLOAD_SIZE` → «Файл больше»),
test_attachment_kind_comment_rejected_from_card.

**TaskChecklistActionViewTests (+2):** test_toggle_htmx_fragment_by_assignee
(фрагмент, «1 / 2», отметка в БД), test_toggle_htmx_denied_for_viewer
(фрагмент с ошибкой, состояние не меняется).

### 7.4. `makemigrations --check`

Как и на предыдущих этапах: 2 label-only AlterField
(`Document.suitability_code`, `Folder.zone`) — **предсуществующий дрейф**
на чистом `d94e09f` без патча, к Этапу 4 и раунду аудита отношения не
имеет (модели не тронуты). Рекомендация отдельного `0032` в силе.

## 8. Скриншоты (`docs/audit/design-task-stage4/screenshots/`)

| Файл | Что показывает |
|---|---|
| `01…07_*.png` | Раунд 1 (реализация): карточки статусов «В работе»/«На проверке»/«Принято»/черновик, ошибка «Выполнено» без output, вкладка со ссылками |
| `08_audit_card_top_htmx.png` | Раунд 2: шапка после REWORK — «В работе», «Критический», красный «просрочено на 3 дня» с иконкой; Суть; Входные данные (AR-01 — ссылка на документ); Результат с формой (спиннер в кнопке); Чек-лист 2/3 |
| `09_audit_checklist_htmx_toggled.png` | HTMX toggle: после клика «3 / 3», зелёный alert «Пункт «Согласовать с ГИПом» отмечен.», пункт зачёркнут с done_by/done_at — без перезагрузки страницы |
| `10_audit_upload_reject_fragment.png` | HTMX-валидация: `.exe` → красный alert «Формат «exe» не поддерживается. Разрешены: …» внутри карточки «Результат»; вложение не создано |
| `11_audit_upload_success_fragment.png` | HTMX-загрузка: `result.pdf` → «Результат: вложение добавлено.», счётчик «Результат 2», файл в списке — фрагмент обновлён без перезагрузки |
| `12_audit_document_related_tasks.png` | Карточка документа AR-01: блок «Связанные задания 1» — «#3 Передать нагрузки от перегородок», бейдж «В работе», исполнитель, срок |

Скриншоты сняты в Chromium 1440×900 на dev-сервере с демо-данными
проекта `DT-UI` (сценарий `scripts/take_stage4_audit_screenshots*.sh`,
в репозиторий не входит).

## 9. Выводы и рекомендации

**OK без правок:** структура вьюх и URL, тонкость адаптера (матрица/права
только в сервисах), состав блоков и панель действий, гейды доступа
(404 чужим), CSRF, вкладка→карточка, история с бейджами, терминальные
статусы, тестовое покрытие раунда 1.

**REWORK (все исправлены в `design-task-stage4-audit.patch`):**
R1 поллинг дедлайна, R2 HTMX-чек-лист, R3 «Связанные задания»,
R4 индикатор загрузки + типы файлов, R5 kind-валидация карточки.

**Рекомендации (вне рамок Этапа 4):**

1. Отдельная миграция `0032` для предсуществующего label-only дрейфа
   (`Document.suitability_code`, `Folder.zone`) — чтобы `makemigrations
   --check` был чистым на всех стендах.
2. `kind=comment` у `add_task_attachment` не имеет UI в карточке —
   либо убрать из сервиса на время UI-этапа, либо сделать блок
   «Комментарии/обсуждение» отдельным этапом.
3. Построчный прогресс загрузки (XHR `upload.onprogress`) требует
   JS-файла — целесообразно на этап «полировка UX», если ГИПу
   регулярно грузятся тяжёлые чертежи.
4. Тосты фрагментов живут до перезагрузки — при желании можно
   автозакрывать успех через `hx-swap-oob` (в рамках ограничений
   этапа не делалось).

**Не коммитить до APPROVE Координатора.**
