# Отчёт Аудитора: Этап 6 — Дашборд ГИПа (патч)

**База:** `main`, HEAD = `7fd3f92` (после Email-фикса SMTP Timeweb).
**Артефакт:** `design-task-stage6.patch` — применяется Кодером командой
`git apply design-task-stage6.patch` (проверено на чистом клоне 7fd3f92).
**План:** `docs/DESIGN_TASKS_STAGE6_PLAN.md` (APPROVE Координатора, решения D1–D5 приняты).
**Спека:** `docs/DESIGN_TASKS_SPEC.md` п. 4.4. Дата: 2026-11-16.

## Состав патча (7 файлов, +1083/−17)

| Файл | Изменение |
|---|---|
| `storage/domain/services.py` | Константа `_NOT_TERMINAL_TASK_STATUSES` (микро-рефактор `task_summary_for_gip` — поведение идентично, дублирование устранено; `_NOT_CLOSED_TASK_STATUSES` Этапа 5 не тронут) + новая функция `tasks_requiring_attention(project)`: один запрос с `select_related`, причины по приоритету `overdue → review → on_hold` (D2), сортировка группа → срок asc → pk |
| `storage/adapters/views.py` | `TaskDashboardView` (сводка + «Требуют внимания» + чипы 9 статусов, D3) и `TaskDashboardExportView` (xlsx по образцу реестра замечаний: жирная шапка, hyperlink карточки, красная просрочка, `reestr_zadanij_<code>.xlsx`); гейт `can_view_all_tasks` (403) + `get_accessible_projects` (404); в контекст вкладки добавлен `can_view_all_tasks` (additive) |
| `storage/adapters/urls.py` | `projects/<int:project_id>/tasks/dashboard/` и `.../dashboard/export/` — объявлены до `tasks/<int:task_id>/` |
| `storage/adapters/templates/storage/task_dashboard.html` | Новый: 5 карточек сводки (просрочка красная при >0), чипы статусов с переходами `?tab=tasks&status=X`, таблица внимания с колонкой «Причина» (D1) и пустым состоянием; только Bootstrap/redesign.css, без JS |
| `storage/adapters/templates/storage/project_detail.html` | Кнопка «Дашборд» в тулбаре вкладки «Задания» (только для ГИП/админ) |
| `storage/tests/test_design_tasks_dashboard.py` | Новый: 24 теста — матрица прав, сводка (граница due==today, терминальные вне overdue), внимание (приоритет причины, без дублей, порядок групп), экспорт (заголовки, только открытые, hyperlink, красный шрифт, пустой проект), кнопка-вход, `assertNumQueries` (сервис 1+3, страница 12) |
| `docs/PROGRESS.md` | Запись Этапа 6 (Шаг 6 плана) |

Модели не менялись, миграций ноль (`makemigrations --check` → «No changes detected»).
Celery/beat Этапа 5 не затронуты. Отклонение от буквы плана одно: `status_badge`
строк таблицы проставляется во вьюхе, а не в сервисе — `TASK_STATUS_BADGES`
живёт в слое adapters, импорт views→services невозможен (слой домена не зависит от UI).

## Верификация

1. `manage.py check` — 0 проблем (в рабочей копии и на чистом клоне).
2. `manage.py makemigrations --check` — ноль миграций.
3. `python manage.py test storage.tests.test_design_tasks_dashboard --noinput` — **24/24 OK** (в рабочей копии и на чистом клоне после `git apply`).
4. Смоук (чек-лист п. 3): `create_project_from_template('residential')` → разделы АР/КР, задания IN_PROGRESS (просрочено)/ON_HOLD/REVIEW → карточки `total=3, in_progress=1, awaiting_review=1, overdue=1, on_hold=1`, таблица внимания `overdue → review → on_hold`, экспорт открывается openpyxl (шапка + 3 строки, `SMK6` в имени), исполнитель: страница 403, кнопки «Дашборд» на вкладке нет.
5. RuntimeWarning в тестах Этапа 6 — нет.

## Важно: 9 предсуществующих падений на 7fd3f92 (НЕ от Этапа 6)

Полный регресс `storage.tests issues` (598 тестов) даёт 3 failures + 6 errors.
Проверено на **чистом 7fd3f92 без патча** — тот же набор:

- **6 errors** (`test_demo_command` ×4, `test_sprint7.DemoCommandGuardTests` ×2):
  `ValueError: Раздел с таким кодом уже существует` в `create_section` — конфликт
  демо-команды с sections-sync (d699413): Section'ы создаются дважды.
- **3 failures** (`test_design_tasks_views`: `test_create_form_get_ok`,
  `test_create_form_get_without_write_access`, `test_tasks_tab_issue_button_rights`):
  тесты Этапа 3 ассертят доступ к форме по write-праву раздела, а f23b548 перевёл
  гейт GET на ролевой `_can_issue_task` (только ГИП/админ) — developer с write-правом
  в АР теперь получает 302 на `?tab=tasks`.

Этап 6 эти тесты не трогает и новых падений не добавляет. Рекомендация: отдельный
маленький патч согласования тестов с f23b548 + починка демо-команды (до беты).

## Приёмка

Применение: из корня репо на 7fd3f92 — `git apply design-task-stage6.patch`,
затем `manage.py check` / `makemigrations --check` / тесты дашборда. Смоук-сценарий
ручной: проект с заданиями во всех статусах → дашборд `/projects/<id>/tasks/dashboard/`.
