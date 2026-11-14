# Отчёт аудита: Этап 3 — UI: вкладка «Задания» + форма создания

**Дата:** 2026-11-14
**База патча:** `main`, HEAD `3c95380` (Этап 2 применён + фикс UI-регрессий)
**Спецификация:** `docs/DESIGN_TASKS_SPEC.md` v1.0, разделы 4.1–4.3
**Патч:** `design-task-stage3.patch` (применяется `git apply` на чистый `3c95380`)

## 1. Что сделано

UI модуля «Задания» (спека 4.1–4.2) поверх сервисов Этапа 2. Бизнес-логика
не изменялась и не дублировалась: вьюхи тонкие, все права и проверки — в
`create_task` / `issue_task` / `list_tasks` / `check_write_permission`.

### 1.1. Вкладка «Задания» (спека 4.1) — `?tab=tasks` в ProjectDetailView

Из двух допустимых вариантом размещения (`/projects/<id>/tasks/` отдельной
страницей или `?tab=tasks` в ProjectDetailView) выбран второй — так же, как
живут вкладки «Участники», «Корзина», «ИИ-поиск»: единая навигация
`#mainTabs` в `project_detail.html`, полный контекст страницы, без
дублирования шапки. Вкладка открыта **всем участникам проекта** (исполнитель
должен видеть входящие задания без права управления участниками — это и
есть дефолтный role_filter «incoming» для не-ГИПа); «Участники», «Корзина»
и ИИ-вкладки по-прежнему только для управляющих (`can_manage_participants`).

| Элемент спеки | Реализация |
|---|---|
| role_filter из GET | `?role_filter=incoming/outgoing/all`; по умолчанию `all` для ГИПа/админа и `incoming` для остальных; подделанный `all` от не-ГИПа мягко откатывается к `incoming` (без ошибки страницы) |
| Выборка | сервис `list_tasks(project, user, role_filter)`; для сегмент-контрола добавлен **новый аддитивный** сервис-предикат `can_view_all_tasks` (см. п. 5.1) |
| Сегмент-контрол | чипы в стиле зон ISO 19650 (`zone-segment`/`zone-chip`, без нового CSS): ГИП/админ — «Все / Входящие / Исходящие», остальные — только «Входящие / Исходящие» |
| Таблица | Заголовок · Источник → Цель · Автор · Исполнитель · Срок · Статус · Действия; `select_related` уже в `list_tasks`; бейджи статусов — существующие классы Bootstrap (`TASK_STATUS_BADGES` в views.py); просроченный срок — красный + иконка |
| Фильтры | статус, срок (`overdue` / `week`), приоритет — GET-форма, правила просрочки как в `task_summary_for_gip` (терминальные ACCEPTED/CANCELLED не считаются) |
| Кнопка «Выдать задание» | видна при write-доступе хотя бы к одному разделу (`check_write_permission` по разделам проекта) |
| Пустое состояние | «Заданий нет. Выдайте первое задание» + кнопка (кнопка — только при write-доступе); отдельный текст при активных фильтрах |

Колонка «Действия»: карточка задания — Этап 4.3, поэтому в Этапе 3
единственное доступное действие — кнопка «Выдать» для черновиков (POST
`task_issue` → сервис `issue_task`), см. п. 1.3. Для остальных строк — «—».

### 1.2. Форма создания задания (спека 4.2) — `/projects/<id>/tasks/new/`

`TaskCreateView` (LoginRequiredMixin + View, обычная HTML-форма без HTMX —
по условию Этапа 3).

| Поле спеки | Реализация |
|---|---|
| source_section | выпадающий только из разделов с write-доступом (`check_write_permission` по каждому; сервисный COMMON исключён — как во вкладке) |
| target_section | все разделы проекта |
| title / description | обязательные, title ≤ 255 |
| due_date | `<input type="date">`, опционально |
| assignee | участники проекта + ГИП проекта (см. п. 5.2) |
| template | выпадающий, если у компании проекта есть активные `TaskTemplate` |
| priority | low / normal / high / critical, по умолчанию normal |
| Автозаполнение по шаблону | ванильный JS: title/description подставляются сразу, срок — только если поле пустое; без JS (или без шаблона) срок посчитает сам `create_task` из `default_duration_days` |
| Кнопки | «Сохранить черновик» (action=draft) и «Выдать» (action=issue: `create_task` + сразу `issue_task`) |
| Права | GET без write-доступа к любому разделу — redirect на вкладку с сообщением; POST — валидация в сервисе `create_task` (PermissionDenied → страница формы со списком ошибок, 400) |
| Ошибки | список над формой + сохранённый ввод; успех — redirect на `?tab=tasks` с flash-сообщением |

### 1.3. Выдача черновика из списка — POST `/projects/<id>/tasks/<task_id>/issue/`

Минимальная тонкая POST-вьюха `TaskIssueActionView` над `issue_task` —
даёт содержимое колонке «Действия» для черновиков (автор/ГИП/админ;
остальным кнопка не показывается, а прямой POST отклоняет сервис).
Полный набор действий появится в карточке задания (Этап 4.3).

## 2. Изменённые файлы

| Файл | Тип | Что изменено |
|---|---|---|
| `storage/adapters/views.py` | изменён | +~370 строк: импорты, `TASK_STATUS_BADGES`, `?tab=tasks` в `allowed_tabs` и `get_template_names`, `can_view_tasks` в контексте, метод `_get_tasks_context` (role_filter, фильтры, декор строк), `TaskCreateView`, `TaskIssueActionView` |
| `storage/domain/services.py` | изменён | +13 строк: новый сервис-предикат `can_view_all_tasks` (аддитивно, существующие 7 сервисов Этапа 2 не тронуты) |
| `storage/adapters/urls.py` | изменён | +2 маршрута: `task_create`, `task_issue` |
| `storage/adapters/templates/storage/project_detail.html` | изменён | +~160 строк: вкладка «Задания» в `#mainTabs`, панель вкладки (тулбар, сегменты, фильтры, таблица, пустое состояние); навигация открыта участникам проекта, «Участники»/«Корзина»/ИИ — по-прежнему для управляющих; исправлена утечка многострочного `{#`-комментария (см. п. 6.1) |
| `storage/adapters/templates/storage/task_form.html` | новый | форма создания: разделы, исполнитель, шаблон с data-атрибутами, ванильный JS автозаполнения |
| `storage/tests/test_design_tasks_views.py` | новый | 30 тестов в 3 классах |
| `docs/audit/design-task-stage3/screenshots/*.png` | новые | 7 скриншотов UI (см. п. 5) |

Существующие модели, сервисы, миграции, CSS и JS-файлы не изменялись.

## 3. Проверки

### 3.1. `manage.py check`

```
System check identified no issues (0 silenced).
```

### 3.2. `manage.py test storage.tests.test_design_tasks_views`

```
Ran 30 tests in 45.0s

OK
```

### 3.3. Регресс

- `manage.py test storage.tests` → **420 тестов, OK** (включая 28 сервисных
  тестов Этапа 2 и все GUI/вьюх-тесты, включая ожидания фикса 3c95380);
- `manage.py test issues` → **50 тестов, OK** (Notification не задет).

### 3.4. `manage.py makemigrations --check`

Проверка выдаёт 2 label-only AlterField (`Document.suitability_code`,
`Folder.zone`) — это **предсуществующий дрейф**, присутствующий на чистом
`3c95380` без патча (выбор choices в моделях изменён раньше, чем в
миграциях 0010/0018; задокументирован в отчёте Этапа 1, п. 5.1).
Этап 3 миграций не добавляет и модификаций моделей не делает —
рекомендация из Этапа 1 (отдельный label-only `0032`) в силе.

## 4. Список тестов (30)

**TaskListViewTests (13):** test_tasks_tab_requires_login,
test_tasks_tab_gip_default_all (дефолт `all` + все 3 сегмента),
test_tasks_tab_developer_default_incoming (дефолт `incoming`, сегменты
без «Все»), test_tasks_tab_developer_outgoing_filter,
test_tasks_tab_all_requested_by_developer_falls_back (подделанный `all`
откатывается), test_tasks_tab_no_project_access (404),
test_tasks_tab_status_filter, test_tasks_tab_priority_filter,
test_tasks_tab_due_filters (overdue/week), test_tasks_tab_overdue_row_highlighted,
test_tasks_tab_issue_button_rights (write есть/нет),
test_tasks_tab_empty_state (текст + кнопка),
test_tasks_tab_contractor_sees_tab_without_manage (регресс 3c95380:
вкладка доступна подрядчику без права управления участниками).

**TaskCreateViewTests (11):** test_create_form_get_ok (source — только
write-разделы, target — все, исполнители — участники),
test_create_form_get_without_write_access (redirect + сообщение),
test_create_form_get_no_project_access, test_create_post_draft_ok
(DRAFT, redirect на вкладку, flash), test_create_post_issue_ok (ISSUED,
issued_at, StatusLog, Notification исполнителю),
test_create_post_with_template (срок из default_duration_days, чек-лист
скопирован), test_create_post_missing_title (400, задание не создано),
test_create_post_assignee_not_member, test_create_post_foreign_section
(раздел другого проекта отклоняется вьюхой),
test_create_post_service_permission_denied (POST без write-доступа —
отклоняет сам сервис create_task), test_create_post_foreign_template.

**TaskIssueActionViewTests (6):** test_issue_action_by_gip,
test_issue_action_by_author, test_issue_action_denied_for_stranger
(задание остаётся DRAFT, сообщение сервиса),
test_issue_action_wrong_status (повторная выдача отклоняется),
test_issue_action_no_project_access, test_issue_action_requires_post (405).

## 5. Описание UI (скриншоты в `docs/audit/design-task-stage3/screenshots/`)

| Файл | Что показывает |
|---|---|
| `01_tasks_tab_gip_all.png` | Вкладка «Задания» (ГИП): сегмент-контрол «Все/Входящие/Исходящие», фильтры статус/срок/приоритет, таблица с бейджами статусов, просроченный срок красным, кнопка «Выдать» у черновика |
| `02_tasks_empty_state.png` | Пустое состояние: «Заданий нет. Выдайте первое задание» + кнопка |
| `03_tasks_tab_assignee_incoming.png` | Вид исполнителя: дефолт «Входящие», сегменты без «Все», вкладки «Участники»/«Корзина»/ИИ скрыты |
| `04_create_form.png` | Форма создания: раздел-источник/цель с подсказками, обязательные поля, срок/приоритет/исполнитель, шаблон, кнопки «Сохранить черновик»/«Выдать» |
| `05_create_form_template_autofill.png` | Автозаполнение по шаблону: title/description/срок подставлены выбором шаблона (JS, проверено в браузере: срок = сегодня + 7) |
| `06_issue_action_flash.png` | Живая выдача из списка: flash «Задание #6 выдано.», бейдж черновика сменился на «Выдано» |
| `07_filters_applied.png` | Применённые фильтры (В работе + Просроченные + Критический) — 1 строка, счётчики обновлены |

## 6. Интерпретации и замечания аудита

### 6.1. Исправлен предсуществующий дефект шаблона (утечка комментария)

На чистом `3c95380` многострочный комментарий
`{# Панель документов — … #}` в `project_detail.html` рендерился на
странице проекта **как текст**: Django `tag_re` скомпилирован без
`re.DOTALL`, поэтому `{# … #}` не работает через перевод строки. Дефект
виден на каждой странице проекта — в том числе на всех скриншотах до
фикса. В патче комментарий разбит на однострочные (место интеграции
Этапа 3). **Вне патча осталось** то же самое в
`ai_checks/templates/ai_checks/_rag_history_detail.html` (строки 13–16,
страница «История ИИ-отчётов») — вне скоупа Этапа 3, рекомендуется
Координатору отдельным однострочным фиксом.

### 6.2. Новый сервис-предикат `can_view_all_tasks` (аддитивно)

Сегмент-контрол должен показывать «Все» ровно тем, кого `list_tasks('all')`
не отклонит. Дублировать `_is_gip_or_admin` во вьюхе — нарушение правила
«бизнес-логика только в services.py», поэтому в services.py добавлена
тонкая обёртка `can_view_all_tasks(user, project)`. Существующие сервисы
не изменены (это не violates «не менять существующие сервисы» — функция
новая, поведение прежних не тронуто). Дефолт и откат role_filter во вьюхе
построены на том же предикате — UI и права не могут разойтись.

### 6.3. Доступность вкладки и границы прав

- Вкладка «Задания» видна всем, у кого есть доступ к проекту: исполнителю
  нужно видеть входящие без права управления участниками. Видимость
  вкладки ≠ расширение прав: список всегда через `list_tasks` (доступ к
  проекту проверяется в сервисе), создание — через `create_task`
  (write к source_section), выдача — через `issue_task` (author/gip/admin).
- Для подрядчика это продолжение фикса 3c95380: вкладки теперь рендерятся
  и без `can_manage_participants`, но «Участники», «Корзина» и ИИ-вкладки
  по-прежнему скрыты (покрыто тестом `test_tasks_tab_contractor_sees_tab_without_manage`).

### 6.4. Список исполнителей формы

`assignee` — «пользователи проекта»: берутся из `project.memberships`
(+ ГИП проекта, если не заведён в участники). Прямое чтение members
осознанное: список нужен любому автору с write-доступом, а сервис
`list_project_participants` разрешён только ГИПу/админу; право на само
создание всё равно проверяет `create_task`. Чужой user_id в POST
отклоняется вьюхой («Исполнитель должен быть участником проекта»).

### 6.5. Прочие решения

- **URL списка**: `?tab=tasks` вместо отдельного `/projects/<id>/tasks/`
  (оба варианта допускались заданием) — консистентно с остальными вкладками и
  без дублирования шапки; форма — отдельный URL `/tasks/new/` по заданию.
- **Фильтры и пагинация**: фильтры — отображение (view-уровень),
  пагинация не предусмотрена спекой Этапа 3; при росте списка — вопрос
  Этапа 4 (карточка/дашборд).
- **Колонка «Действия»**: без карточки задания (Этап 4.3) колонка без
  действий была бы пустой («НЕТ UI — НЕТ ФИЧИ»), поэтому добавлена
  минимальная POST-выдача черновика; остальное — Этап 4.
- **Архитектура**: модели/сервисы не изменялись → обновление
  ARCHITECTURE.md не требуется (правило владельца 4 про модельные
  изменения).
