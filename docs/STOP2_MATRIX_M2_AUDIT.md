# СТОП-2: итоговый аудит M2 (ORM-агрегация «Матрицы заданий») — **ВЕРДИКТ: APPROVE**

**Автор:** Аудитор (Z.ai / GLM-5.3-Flash, режим Debug).
**Дата:** 2026-11-20.
**Объект:** коммит `921d46e657ee8465723d31dbe27112be55bece9b` «feat(tasks): tasks matrix ORM aggregation (M2)» (автор ABEL, 2026-11-19 22:35 +0300), ветка `m2-tasks-matrix` (origin), родитель `c0e5ee7`.
**Предшественники:** `STOP1_MATRIX_M1_AUDIT.md` (APPROVE M1), `STOP2_MATRIX_M2_PART1.md` (база M1, ответ на вопрос 2), `STOP2_MATRIX_M2_PART2_BLOCKED.md` (недоставка файловых вложений — подтверждена; материалы получены через git-ветку, как предложено Координатором).
**Метод:** чек-лист §4 части 1 (п. 1–8) + п. 9 BLOCKED-протокола; сверка план→факт со строками; независимая машинная верификация (собственный сценарий на живой БД, метод СТОП-1) + полный регресс в пересобранной среде. Код не менялся.

---

## 1. Верификация дифа (п. 8 задания)

1. `git show --stat 921d46e` — **ровно 3 файла, +473/−0**: `services.py` +88, `tasks_matrix.py` +14, `test_tasks_matrix_service.py` +371 (новый). Посторонних файлов нет; `storage/migrations/` не тронут (К2 соблюдён).
2. Родитель коммита = `c0e5ee7` (согласованная база); `git ls-remote` — `main = c0e5ee7` не тронут (мерж после вердикта, как приказано), единственная ветка `m2-tasks-matrix = 921d46e`.
3. **Объяснение +88 вместо заявленных +85 подтверждено:** +11 строк импорт-блока (`services.py:62–72`: 7 составов `CELL_*`, `COUNTER_KEYS`, `compute_cell_status`) + 77 строк сервиса (`:5452–5526`), из них 3 строки — маркер-комментарий служебных ключей (`:5509–5511`), т. е. принятие пожелания части 1 §3.
4. **Чистое ядро M1 не изменено:** diff `tasks_matrix.py` = только +14 (`COUNTER_KEYS`, :61–74); `compute_cell_status` и все составы `CELL_*` байт-в-байт из коммита M1 `40abc27` — текст СТОП-1 в силе.
5. Рабочее дерево ветки чистое (mode-шум песочницы подавлен `core.filemode=false`; единственные untracked — 3 протокола Аудитора в `docs/`, коммитятся отдельным docs-sync, в M2-коммит не входят).

## 2. Сервис vs §3.3/§3.5 (п. 1 задания) — сверки план→факт

| # | Требование плана | Факт | Статус |
|---|---|---|---|
| 1 | Позиция: после `tasks_requiring_attention`, до `can_view_all_tasks` (§3.1, план :93) | def `tasks_matrix_for_project` `:5452`, `can_view_all_tasks` `:5529` (до-M2 :5374/:5441, сдвиг +11 импортом) | OK |
| 2 | `today=None → timezone.localdate()` (Р7, прецедент до-M2 `:5358`) | `:5468–5469` | OK |
| 3 | Оси: `filter(project).exclude(code='COMMON').order_by('order','code')` (§3.3.1) | `:5471–5475`; прецеденты фактом подтверждены: exclude — тело `get_sections_with_counts` `:561–563`, order_by — `:323` | OK |
| 4 | Один GROUP BY: `values('source_section_id','target_section_id').annotate(...)` (§3.3.2) | `:5479–5497`; префикс индекса `design_task_proj_src_tgt_idx` = (project, source_section, target_section, …), `models.py:807–814` — WHERE `project_id` + GROUP BY по 2-му/3-му столбцу (§3.5) | OK |
| 5 | Составы счётчиков = импорт констант ядра, без второго объявления | импорт `:62–72`, аннотации `:5483–5495` | OK |
| 6 | Предикат просрочки = правило 2 К1: `due_date < today` И статус ∈ OVERDUE-набор | `Q(due_date__lt=today, status__in=CELL_OVERDUE_STATUSES)` `:5485–5491`; строгое `<` (due==today не просрочка — тест + независимая B-граница); NULL исключён SQL `__lt` | OK |
| 7 | `non_cancelled` = не-CANCELLED (Ф3, Р6-whitelist) | `~Q(status__in=CELL_CANCELLED_STATUSES)` `:5478` + `:5483`; `CELL_CANCELLED_STATUSES={'CANCELLED'}` (`tasks_matrix.py:39`); будущий REQUESTED попадёт только в `total`/`non_cancelled` — семантика Р6 сохранена | OK |
| 8 | Пары с COMMON отброшены | Python-фильтр `:5503–5505` (по `section_ids`) | OK |
| 9 | dict-вход `compute_cell_status` (З2 СТОП-1) | `counters = {key: row[key] for key in COUNTER_KEYS}` `:5506` → вызов `:5515`; машинно: ядро читает ровно реестр через `.get()` | OK |
| 10 | Гейт прав не в сервисе (§3.1) | докстринг `:5465–5466`; прецедент гейта подтверждён: `storage/adapters/views.py:1888` | OK |

Возврат дословно §3.2: `{'sections': […], 'cells': {(src_id, tgt_id): cell}, 'today': date}` (`:5526`); ячейки — только пары с ≥1 задачей (GROUP BY), пара из одних CANCELLED присутствует с `total=0`/NOT_STARTED (Ф1) — согласовано с §6.2.

## 3. §9.1-отклонения и п. 9 чек-листа (п. 2 задания)

1. **dict-ячейка вместо NamedTuple (§3.2/§9.1)** — зафиксированное, одобренное отклонение (З2 СТОП-1; ответ на вопрос Координатора 2 в части 1 §3). Реализовано; более того, ядро **требует** dict: `compute_cell_status` читает через `counters.get(key, 0)` — у NamedTuple `.get()` нет, альтернативы не было. **M3-совместимость подтверждена:** в Django-шаблоне `{{ cell.status }}` на dict резолвится как `cell['status']` (порядок lookup: словарь → атрибут); в python-коде вьюхи/тестов — только `cell['status']` (требование к M3 из части 1 §3). Код документирует это в `:5507–5508`.
2. **Дубли `total`/`non_cancelled`** — оба `~CANCELLED` и равны по построению (`:5516` = `counters['non_cancelled']`; машинно: равенство на всех 4 живых ячейках). Решение части 1 §3 («оставить оба: total — К3/отображаемый, non_cancelled — правило 1/служебный») реализовано как решено.
3. **Сверх-счётчики `returned`/`on_hold`/`review`** — служебные правила 3/4/5 (план :120), оставлены — решение части 1 подтверждено кодом (`:5520–5523`).
4. **П. 9: маркеры «не для шаблона»** — есть, ровно у 4 служебных ключей (`non_cancelled`, `returned`, `on_hold`, `review`), `:5509–5511`; **отображаемых ровно 4** (`total`, `in_progress`, `overdue`, `done`, `:5516–5519`). Состав ключей ячейки = 4 отображаемых + 4 служебных + `status` + 2 id, лишнего нет, служебные не пересекаются с отображаемыми — машинно (B10/B13–B15).

## 4. Тесты vs §6.2 (п. 3 задания)

27 тестов = 24 (`TasksMatrixForProjectTests`) + 3 (`TasksMatrixQueryCountTests`). Все 7 задекларированных планом кейсов присутствуют; сверх плана — граничные из §6.1 и п. 6 чек-листа:

| Кейс §6.2 (план :249–257) | Тест |
|---|---|
| пустой проект: оси есть, `cells` пусто | `test_empty_project_has_axes_and_no_cells` :101 |
| одна пара → одна ячейка, счётчики факту | `test_single_pair_gives_single_cell` :108 + `test_counters_match_facts` :123 |
| COMMON вне осей при любом объёме задач | `test_axes_exclude_common` :87 + `test_task_with_common_section_not_in_cells` :116 |
| сортировка осей (order, code) | `test_axes_sorted_by_order_then_code` :94 (тай-брейк code: AA/ZZ при order=5) |
| пара после CANCELLED → NOT_STARTED, total==0 (Ф1/Ф3) | `test_f1_pair_of_only_cancelled_is_not_started` :235 |
| диагональ source==target обычной ячейкой (У3-б) | `test_diagonal_pair_is_regular_cell` :281 |
| today-параметр сдвигает просрочку детерминированно | `test_today_parameter_shifts_overdue` :208 (без правки данных) |

Сверхплановые: границы `due==today`/`yesterday` (:184/:192), NULL `due_date` (:201), REVIEW гасит просрочку (:224), Ф2-микс DRAFT+CANCELLED (:245), частичное завершение `done=1/total=2` (:254), оба направления пары (:266), изоляция проекта (:294). Все 7 статусов ячейки — на живых данных (:144–180).

**Запросы:** `assertNumQueries(2)` на прямой вызов сервиса (не через вьюху) :347–351; ровно один GROUP BY через `CaptureQueriesContext` :353–360; GROUP BY фильтрован по проекту (`"project_id"` в SQL) :362–371. Критерий §7 M2 «проверить `len(connection.queries)` в тесте» выполнен **эквивалентной и более строгой** реализацией (`assertNumQueries`/`CaptureQueriesContext` работают и при DEBUG=False, где `connection.queries` пуст) — засчитано по смыслу критерия.

## 5. Независимая машинная верификация (п. 4 задания)

1. **Среда:** `.venv` клона (Python 3.12.16, Django 6.1.2, requirements без `onnxruntime`/`transformers`) — та же, что в части 1; сохранена в клоне.
2. **Скрипт `scripts/stop2_m2_verify.py` (метод СТОП-1: собственный сценарий, не копия тестов Кодера) — 71/71 OK:**
   - **A-блок (ядро×реестр):** `COUNTER_KEYS` == 7 Ф3-ключей без дублей; инструментированный dict (перехват `.get`) — ядро читает **ровно** ключи реестра; цепочка доходит до правила 7 (микс non_cancelled=2/done=1/in_progress=1 → IN_PROGRESS).
   - **B-сценарий (живой sqlite):** 13 задач, 2 проекта, оси AR/KR/GS+COMMON — ручной расчёт 4 ячеек vs сервис: **0 расхождений** по всем 9 полям каждой ячейки; порядок цепочки подтверждён на живых данных (RETURNED с прошедшим сроком → **OVERDUE**, не REWORK — правило 3 раньше правила 4); **замыкание** `compute_cell_status({k: cell[k] for k in COUNTER_KEYS}) == cell['status']` на всех 4 ячейках (§3.2 :122); `total == non_cancelled` на всех ячейках; ровно 2 запроса, 1 GROUP BY c `project_id`; `today+1` → overdue 3→4 (граница due==today); `today=None → localdate()`.
   - Нотация: промежуточный FAIL A5/A6 — дефект моего же инструментирования (`__getitem__` вместо `.get`), не кода; после исправления 71/71.
3. **Регресс дифа:** `manage.py check` — «System check identified no issues (0 silenced)»; `manage.py test test_design_tasks test_design_tasks_views test_design_tasks_dashboard test_design_tasks_notify test_tasks_matrix test_tasks_matrix_service --parallel 1` → **Ran 230 tests in 232.621s, OK** — точно предсказанная сумма 170 (28+87+24+31 по §6.4) + 33 (M1) + 27 (M2).

## 6. Index-гигиена (п. 5 задания)

Коммит = ровно 3 M2-файла; рабочее дерево чистое; в push-области только `m2-tasks-matrix = 921d46e`, `main` не тронут. Протоколы Аудитора (`STOP2_MATRIX_M2_PART1.md`, `STOP2_MATRIX_M2_PART2_BLOCKED.md`, настоящий) — untracked в `docs/`, идут отдельным docs-sync по правилам репо (З5-аналог СТОП-1).

## 7. Замечания (ни одного блокирующего)

- **ЗM2-1 (желательное):** в дифе нет явного теста-замыкания `compute_cell_status({k: cell[k] for k in COUNTER_KEYS}) == cell['status']` (п. 6 чек-листа; §3.2 :122 «перепроверяемы тестами»). Статус вычисляется из тех же данных по построению (`:5506`→`:5515`), 7 статусов покрыты живыми кейсами, замыкание независимо подтверждено моим скриптом (B11, 4 ячейки). Рекомендация: +1 тест-строка на СТОП-5/M5. Не блокирует.
- **ЗM2-2 (косметика, правка не требуется):** ссылка докстринга «прецедент …, :550–551» дана в до-M2 нумерации `services.py` (после +11 импорта фактически :561–563); «views.py» без пути — фактический файл `storage/adapters/views.py` (гейт :1888 подтверждён). Сверка прецедентов выполнена по факту, выводы не меняются.
- **ЗM2-3 (опционально):** `test_cells_of_other_project_are_not_mixed` (:294–314) создаёт только чужую пару — утечку поймает (cells не пусто), но сильнее вариант «своя пара + чужая, assert ровно своя». Не блокирует.
- Вне дифа M2 (попутная нота, к Р8-политике терминологии: `storage/adapters/views.py:1890` — текст исключения «Дашборд ГИПа…»; pre-existing, к матрице не относится, править вне отдельного решения Координатора не предлагаю).

## 8. Вердикт

**APPROVE.** Все 9 пунктов чек-листа закрыты; расхождений с планом (§3.1–§3.5, §6.2, §7 M2, §9.1 с одобренным отклонением dict-ячейки) не найдено; независимая верификация 71/71; регресс 230/230; `check` чист; граница дифа «ровно 3 файла» соблюдена; миграций нет; `main` не тронут. M2 готов к мержу в main после вердикта; ЗM2-1…ЗM2-3 — по усмотрению Координатора (все три не блокирующие).

**Наследуется M3 (СТОП-3):** в шаблонах `cell.status` работает свободно (dict-lookup); в python-коде вьюхи/тестов — только `cell['status']`; `cells` — dict с ключ-кортежами `(src_id, tgt_id)`; служебные ключи (`non_cancelled`, `returned`, `on_hold`, `review`) в шаблон не выносить — отображаемых ровно 4 (`total`, `in_progress`, `overdue`, `done`), маркеры `services.py:5509–5511`.

---

*Пруфы текущей сессии: `git ls-remote origin` (ветка/хэши); `git fetch origin m2-tasks-matrix` (локальный refspec клона ограничен main — принесён явно); `git show --stat/--numstat 921d46e`; `git rev-parse 921d46e^`; diff `tasks_matrix.py` против `40abc27`; `sed -n` :62–72/:5452–5526 `services.py`, :806–815 `models.py`, :559–570, :323, :5369–5373 `services.py`, :1883–1890 `storage/adapters/views.py`; `sed -n '240,300p' docs/MATRIX_TASKS_PLAN.md` (§6.2/§7/§8-Р6-Р8); `scripts/stop2_m2_verify.py` 71/71; `manage.py check` 0 issues; `manage.py test …` Ran 230, OK, 232.621 с.*
