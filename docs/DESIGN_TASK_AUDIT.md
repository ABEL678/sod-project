# Аудит: модуль DesignTask против спеки v1.0 + разведка для «Матрицы заданий»

**Проект:** Stream-CAD (СОД), Django 6.1.
**Автор:** Аудитор (Z.ai / GLM-5.3-Flash, режим Debug).
**Дата:** 2026-11-19.
**База:** `github.com/ABEL678/sod-project`, ветка `main`, HEAD = `ed8c2b1a395f36470073b591d7272e1616d3b7d4` (коммит «docs(audit): commit sprint1 patch and fix README path», 18.11.2026 23:18 +0300). Работа велась по **свежему клону** (`/home/z/my-project/work/sod-audit-ed8c2b1`), не по локальным копиям прошлых сессий.
**Спека:** `docs/DESIGN_TASKS_SPEC.md` v1.0 (2026-11-13, 437 строк).
**Объект сравнения №2 (образец):** `github.com/ABEL678/gip-assistant`, свежий клон, HEAD = `366ab96255c472a0a8d3f05259ef3b803c6654fd` (30.10.2026).
**Режим:** только факты и пруфы (файл:строка). Кода не пишем, предложений по коду не даём. Матрица заданий здесь — **внутренняя** матрица взаимодействий смежных разделов проекта (source_section × target_section); RFI (запросы Заказчику, `RfiTask`) в анализ не входит как сущность матрицы.

---

## Сводная таблица «Этап — по спеке — по коду — пруф»

| Этап спеки | По спеке | По коду | Ключевые пруфы |
|---|---|---|---|
| 1. Модели и миграции | 5 моделей, миграция, админка | **Реализован** | models.py:639/697/820/857/915; 0031_design_task_stage1.py:26/53/70/86/103 (+AddConstraint:126, AddIndex:130); admin.py:86/100/108/116/126 |
| 2. Сервисы | 7 функций + матрица переходов + права + лог | **Реализован** (с 3 отклонениями-расширениями) | services.py:4989/5051/5114/5254/5306/5351/5454; матрица :5086–5095; тестов 28 |
| 3. UI: список и форма | вкладка ?tab=tasks, сегменты, фильтры, /tasks/new/ | **Реализован** | project_detail.html:492–646; views.py:1141–1237; TaskCreateView views.py:1359; task_form.html; urls.py:50/55 |
| 4. UI: карточка | блоки Суть/Входные/Результат/Чек-лист/История + действия | **Реализован** | task_detail.html:35–112; HTMX _task_checklist.html:25–26, _task_deadline.html:8–10; TaskDetailView views.py:1767; urls.py:104/109/114/119 |
| 5. Уведомления и напоминания | Notification + 3 Celery-задачи + beat | **Реализован** | services.py:5188–5245 (переходные), :6037/:6056/:6074 (напоминания); tasks.py:18–36; settings.py:315–326; тестов 31 |
| 6. Сводка ГИПа + Excel | сводка, «Требуют внимания», экспорт xlsx | **Реализован** (применён 15.11.2026) | TaskDashboardView views.py:1869, ExportView :1974, ApiView :2124; task_dashboard.html; urls.py:64/69/74; коммиты cfe5b1b + f0514da |
| 7. Шаблоны и чек-листы CRUD | CRUD TaskTemplate «Настройки → Шаблоны» | **Частично** (копирование есть, пользовательского CRUD нет) | копия чек-листа services.py:5037–5041; выборка в форме views.py:1422; админка admin.py:116–123; маршрутов/вьюх/шаблонов CRUD **нет** |
| (8. Интеграции — вне запроса, для полноты PROGRESS) | блок «Связанные задания» + RAG | **Частично** | блок есть: document_detail.html:452–458, views.py:3985–3997; RAG по заданиям — **нет** (DesignTask не упоминается в ai_checks/) |

Итого тестов модуля: `storage/tests/` — test_design_tasks.py (28) + test_design_tasks_views.py (87) + test_design_tasks_dashboard.py (24) + test_design_tasks_notify.py (31) = **170 тестов**.

---

## Часть 1. Этапы 1–7 по коду (детализация с пруфами)

### Этап 1 — модели и миграции: РЕАЛИЗОВАН

Все 5 моделей в `storage/domain/models.py`:

| Модель | Спека §2 | Код |
|---|---|---|
| TaskTemplate | §2.4 | models.py:639–694 |
| DesignTask | §2.1 | models.py:697–817 |
| DesignTaskStatusLog | §2.2 | models.py:820–854 |
| DesignTaskAttachment | §2.3 | models.py:857–912 |
| DesignTaskChecklistItem | §2.5 | models.py:915–947 |

Пруфы соответствия спеке:
- `DesignTask.Status` — ровно 9 значений спеки: DRAFT, ISSUED, IN_PROGRESS, ON_HOLD, COMPLETED, REVIEW, ACCEPTED, RETURNED, CANCELLED (models.py:707–716).
- `DesignTask.Priority` — low/normal/high/critical (models.py:718–722).
- `DesignTaskAttachment.Kind` — input/output/comment (models.py:865–868).
- `TaskTemplate.checklist` — JSONField `default=list` (models.py:679); уникальность `(company, title)` — UniqueConstraint `uniq_tasktemplate_company_title` (models.py:686–691, миграция 0031:126–129).
- Составной индекс спеки §2.1 — `Index(fields=['project','source_section','target_section','title','created_at'], name='design_task_proj_src_tgt_idx')` (models.py:807–814, миграция 0031:130–133).
- Миграция одна: `storage/migrations/0031_design_task_stage1.py` — 5 CreateModel (:25/:52/:69/:85/:102) + AddConstraint (:126) + AddIndex (:130).
- Админка: DesignTaskAdmin (admin.py:86–93), DesignTaskStatusLogAdmin (:100–105), DesignTaskAttachmentAdmin (:108–113), TaskTemplateAdmin (:116–123), DesignTaskChecklistItemAdmin (:126–131) — `list_display`+`list_filter`+`search_fields`, как требовал Этап 1.

Отклонения от буквы спеки (факты, без оценки):
- `DesignTask.external_id` — CharField(100), null, db_index (models.py:793–799). В спеке §2.1 поля нет; спека §7 допускает: «может синхронизироваться с Task/Decision из ГИП-Ассистента через external_id (поле в модели)».
- `DesignTask.assignee` — `on_delete=SET_NULL` (models.py:750–757); спека указывала только `null=True` (on_delete не задан).
- `TaskTemplate.source_section/target_section` — `SET_NULL, null=True, blank=True` (models.py:655–670); спека §2.4: «FK → Section, null=True» (blank в спеке не указан).
- `DesignTaskAttachment.document` — `SET_NULL` (models.py:879–886); спека on_delete не указывала (комментарий в коде: документ мягко удаляется).

### Этап 2 — сервисы: РЕАЛИЗОВАН

Фактический список функций модуля «Задания» в `storage/domain/services.py`:

| Функция | Спека | Строки |
|---|---|---|
| `_design_task_url(task)` | — (внутр.) | 4891 |
| `_is_task_participant(user, task)` | — (внутр.) | 4920 |
| `_notify_task(task, recipient, actor, message, verb=...)` | §5 (обёртка Notification) | 4944 |
| `create_task(...)` | §3 | 4989–5048 |
| `issue_task(user, task)` | §3 | 5051–5080 |
| `_DESIGN_TASK_TRANSITIONS` (словарь) | §3 матрица | 5086–5095 |
| `_DESIGN_TASK_FINAL_STATUSES` | §3 «Любой → CANCELLED» | 5101–5104 |
| `_COMMENT_REQUIRED` | §3 «Условие» | 5107–5111 |
| `update_task_status(...)` | §3 | 5114–5251 |
| `add_task_attachment(...)` | §3 | 5254–5303 |
| `toggle_checklist_item(...)` | §3 | 5306–5331 |
| `_NOT_TERMINAL_TASK_STATUSES` | §4.4 | 5340–5348 |
| `task_summary_for_gip(project)` | §3, §4.4 | 5351–5371 |
| `tasks_requiring_attention(project)` | §4.4 | 5374–5438 |
| `can_view_all_tasks(user, project)` | §3 list_tasks('all') | 5441–5451 |
| `list_tasks(project, user, role_filter)` | §3 | 5454–5483 |
| `_OPEN_TASK_STATUSES` / `_NOT_CLOSED_TASK_STATUSES` | §5 | 5490–5495 / 5500–5507 |
| `_deadline_tasks`, `_reminder_recipients`, `_send_task_deadline_notifications` | §5 | 5510–5628 |
| `notify_tasks_due_soon()` / `notify_tasks_due_today()` / `notify_tasks_overdue()` | §5 | 6037 / 6056 / 6074 |

Матрица переходов — дословно (services.py:5086–5095), роли A=assignee, AU=author, G=gip/admin:

```
(ISSUED → IN_PROGRESS): {'A'}
(IN_PROGRESS → ON_HOLD): {'A'}
(ON_HOLD → IN_PROGRESS): {'A'}
(IN_PROGRESS → COMPLETED): {'A'}
(COMPLETED → REVIEW): {'AU', 'G'}
(REVIEW → ACCEPTED): {'AU', 'G'}
(REVIEW → RETURNED): {'AU', 'G'}
(RETURNED → IN_PROGRESS): {'A'}
```

Реализация матрицы в `update_task_status` (services.py:5114–5251):
- недопустимый переход → ValidationError (:5145–5149); нет прав → PermissionDenied (:5150–5157);
- «Любой → CANCELLED»: ветка :5137–5143, права author/gip/admin, из финальных **запрещено** — `_DESIGN_TASK_FINAL_STATUSES = {ACCEPTED, CANCELLED}` (:5101–5104; комментарий :5097–5100 фиксирует это как интерпретацию «Любой» в отчёте аудита);
- обязательный комментарий: ON_HOLD, RETURNED, CANCELLED (:5107–5111, проверка :5160–5163);
- гейт output-вложения для IN_PROGRESS → COMPLETED (:5165–5171): `task.attachments.filter(kind=OUTPUT).exists()` иначе ValidationError;
- лог DesignTaskStatusLog пишется в атомарной транзакции (:5173–5181, `_log_status_change`);
- уведомления по переходам (:5188–5245): COMPLETED→автору+ГИПу, IN_PROGRESS (принято в работу/возобновлено)→автору+ГИПу, ON_HOLD→автору+ГИПу с причиной, RETURNED→исполнителю с причиной, REVIEW→исполнителю, ACCEPTED→исполнителю, CANCELLED→исполнителю+автору с причиной.

Остальное по Этапу 2:
- `create_task`: права через `check_write_permission(user, source_section)` (:5010–5011) — как требовала спека; разделы проверяются на принадлежность проекту (:5008–5009); статус DRAFT (:5034); срок из `template.default_duration_days` при пустом due_date (:5020–5023); копия чек-листа шаблона bulk_create (:5037–5041).
- `issue_task`: только DRAFT (:5057–5058), только author/gip/admin (:5059–5060), issued_at (:5065), лог (:5067), уведомление assignee verb='assigned' (:5068–5075).
- `add_task_attachment`: input — author или gip/admin (:5274–5278); output — **только assignee** и **только в IN_PROGRESS** (:5279–5285); comment — любые участники через `_is_task_participant` (:5286–5290); требуется document или file (:5270–5271).
- `toggle_checklist_item`: **assignee или gip/admin** (:5316–5317); фиксирует done_by/done_at и сбрасывает при снятии (:5319–5326).
- `list_tasks`: 'incoming' → `assignee=user` (:5479–5480), 'outgoing' → `author=user` (:5481–5482), 'all' → только gip/admin, иначе PermissionDenied (:5468–5471); доступ к проекту обязателен (:5465); select_related (:5472–5477).

Отклонения-расширения относительно буквы спеки (факты):
1. `toggle_checklist_item` — спека §3: «Только assignee»; код: assignee **или** gip/admin (services.py:5316–5317).
2. `add_task_attachment(kind=input)` — спека §3: «input (автор)»; код: author **или** gip/admin (services.py:5274–5278).
3. `add_task_attachment(kind=output)` — спека §3 ограничение «output (исполнитель)» без условия по статусу; код добавляет «только в IN_PROGRESS» (services.py:5282–5285).

### Этап 3 — UI список и форма: РЕАЛИЗОВАН

- Вкладка «Задания» `?tab=tasks` — guard-ветка в `project_detail.html:492–646` (`{% elif active_tab == 'tasks' %}`):
  - тулбар с кнопкой «Выдать задание» (can_issue_task, :500–503) и кнопкой «Сводка» на task_dashboard (can_view_all_tasks, :506–510, подпись «Сводка» — терминология соблюдена);
  - сегмент-контрол «Все / Входящие / Исходящие» (:515–527; сегмент «Все» показывается только ГИПу/админу — задано во вьюхе views.py:1148–1152);
  - GET-фильтры: статус (:534–541), раздел (:542–549), срок «Просроченные/На этой неделе» (:550–559), приоритет (:560–567);
  - таблица «Заголовок | Источник → Цель | Автор | Исполнитель | Срок | Статус | Действия» (:574–631); бейджи приоритета critical/high (:593–597), бейдж статуса `task.status_badge` (:622), просрочка красным + иконка (:614–620), кнопка «Выдать» POST-формой в task_issue (:624–631);
  - пустое состояние с «Сбросить фильтры» (:633–646).
- Контекст вкладки — `ProjectDetailView._get_tasks_context` (views.py:1141–1237): сегменты (:1147–1152), мягкий откат поддельного `?role_filter=all` для не-ГИПа (:1155–1162), фильтры статус/приоритет/срок/раздел (:1172–1213), декор строк — `TASK_STATUS_BADGES` (views.py:275–285), is_overdue по не-терминальным статусам (:1218–1224), can_issue (:1225–1229).
- Форма `/projects/<id>/tasks/new/` — `TaskCreateView` (views.py:1359–1564), шаблон `task_form.html`:
  - поля source/target/title/description/due_date/assignee/template/priority — разбор в `_resolve_input` (views.py:1453–1527); исполнитель — только участник проекта или ГИП (:1503–1515); шаблон — активный, компании проекта (:1519–1526);
  - выпадающий «Шаблон задания» с data-атрибутами (task_form.html:117–131), подсказка «Подставит название, текст и срок; чек-лист скопируется в задание» (:130);
  - автозаполнение по шаблону — ванильный JS (task_form.html:152–180, блок `{% block extra_js %}`); срок подставляется только если поле пустое;
  - кнопки «Сохранить черновик» / «Выдать» (task_form.html:135–144); POST `action=issue` вызывает `issue_task` сразу после `create_task` (views.py:1545–1552), при неудаче черновик сохраняется с предупреждением (:1554–1560).
- Маршруты: task_create urls.py:50, task_issue urls.py:55.
- Тесты: `test_design_tasks_views.py` — 87 тестов (в т.ч. `test_tasks_tab_all_requested_by_developer_falls_back` :176 — откат поддельного role_filter).

### Этап 4 — карточка задания: РЕАЛИЗОВАН

- Маршрут `task_detail` urls.py:104; вьюха `TaskDetailView` views.py:1767 (+ mixin `_DesignTaskCardMixin` :1661, флаги `_task_card_flags` :1690–1732).
- Шаблон `task_detail.html`: шапка — статус-бейдж (:35), приоритет-бейджи (:37–39), «живой дедлайн» HTMX-поллинг `every 60s` (:41–43 → `_task_deadline.html:8–10`); блок «Суть» (:68–72); «Входные данные»/«Результат» — фрагменты `_task_input_card.html`/`_task_output_card.html` (:74–78); «Чек-лист» — фрагмент `_task_checklist.html` (:80–83); «История» — таймлайн `status_logs` с бейджами old→new (:85–112).
- Чек-лист: каждый пункт — `hx-post` на `task_checklist` с `hx-target="#taskChecklistCard" hx-swap="outerHTML"` (`_task_checklist.html:25–26`); ответ — обновлённый фрагмент без перезагрузки; права проверяет сервис `toggle_checklist_item`. Вьюха `TaskChecklistActionView` (views.py:2545–2601): HTMX-фрагмент или классический redirect с messages (прогрессивное улучшение).
- Вложения: загрузка input/output `hx-post` на `task_attachment` (`_task_input_card.html:49–50`, `_task_output_card.html:50–51`, `outerHTML`); привязка к документам — справочник документов проекта в `_task_attachments_context` (views.py:1734).
- Панель действий по правам и статусу — `_task_card_flags` (views.py:1690–1732): can_start_work (ISSUED/RETURNED, assignee), can_hold/can_resume (IN_PROGRESS/ON_HOLD, assignee), can_complete (IN_PROGRESS, assignee), can_send_review (COMPLETED, author/gip), can_review (REVIEW, author/gip), can_cancel (не финальный, author/gip) — с прямым указанием, что «матрица и права проверяются сервисами, вьюха не дублирует».
- POST-вьюхи: TaskStatusActionView (views.py:2403, маршрут urls.py:109), TaskAttachmentActionView (:2428, urls.py:114), TaskChecklistActionView (:2545, urls.py:119).

### Этап 5 — уведомления и напоминания: РЕАЛИЗОВАН

- Переходные уведомления — в `update_task_status`/`issue_task` через `_notify_task` → существующая модель `Notification` (services.py:4944–4987, :5068–5075, :5188–5245). Перечень сообщений совпадает со спекой Этапа 5: «Вам выдано задание #N» (:5073), «ожидает проверки» (:5194), «возвращено на доработку. Причина: …» (:5221–5222), «принято» (:5236).
- Напоминания: `notify_tasks_due_soon` (за 2 дня до due_date, services.py:6037), `notify_tasks_due_today` (в день, :6056), `notify_tasks_overdue` (после, :6074); статусные фильтры `_OPEN_TASK_STATUSES` (:5490–5495) и `_NOT_CLOSED_TASK_STATUSES` (:5500–5507); получатели — исполнитель (+автор+ГИП при просрочке) с проверкой участника проекта (`_reminder_recipients` :5540–5554).
- Celery-задачи: `storage/tasks.py:18–36` — `@shared_task` notify_task_due_soon/due_today/overdue (обёртки над сервисами).
- Расписание celery-beat: `CELERY_BEAT_SCHEDULE` в `sod_core/settings.py:315–326` — notify-task-due-soon (crontab 8:00), notify-task-due-today (8:15), notify-task-overdue (каждый час :00).
- Тесты: `test_design_tasks_notify.py` — 31 тест (идемпотентность, границы, отсутствие утечки за пределы проекта, `test_beat_schedule_has_three_entries` :402).

### Этап 6 — Сводка ГИПа и Excel-экспорт: РЕАЛИЗОВАН

История по git (истина для Части 4): патч Этапа 6 готовился на базе 7fd3f92 (15.11.2026 21:22), применён коммитом **cfe5b1b** «feat(tasks): stage 6 dashboard + fix pre-existing test failures» (15.11.2026 22:31) и отполирован коммитом **f0514da** «feat(dashboard): polish UI (rename to Summary), style Excel, add API for GIP-Assistant» (15.11.2026 22:55).

Состав в HEAD ed8c2b1:
- Вьюхи: `TaskDashboardView` (views.py:1869–1919, гейт `can_view_all_tasks` → 403), `TaskDashboardExportView` (:1974+, тот же гейт), `TaskDashboardApiView` (:2124, UI-API для ГИП-Ассистента).
- Маршруты: `projects/<id>/tasks/dashboard/` (urls.py:64), `.../dashboard/export/` (:69), `.../dashboard/api/` (:74).
- Сводка — `task_summary_for_gip(project)` services.py:5351–5371: total, by_status, overdue (due_date<сегодня и статус в `_NOT_TERMINAL_TASK_STATUSES`), on_hold, awaiting_review.
- «Требуют внимания» — `tasks_requiring_attention(project)` services.py:5374–5438: просроченные + REVIEW + ON_HOLD + **заблокированные активными замечаниями** (M2M `blocking_issues`, issues/models.py:75 — расширение спеки); приоритет причины overdue → blocked → review → on_hold (:5414), сортировка группа → срок asc → pk (:5433–5438).
- Шаблон `task_dashboard.html`: заголовок «Сводка по заданиям смежников» (:10, :20), 5 карточек (всего/в работе/на проверке/просрочено/на уточнении — views.py:1921–1930), таблица внимания с колонкой «Причина» (:70–75), кнопка экспорта (:31–32), плюс блок RFI-счётчиков (:140–159).
- Экспорт Excel: `TaskDashboardExportView` — openpyxl (imports views.py:40–41), только открытые задания (`_NOT_TERMINAL_TASK_STATUSES`), 12 колонок (:2028–2035), жирная шапка, hyperlink на карточку, чередование строк, `sheet.auto_filter`.
- Тесты: `test_design_tasks_dashboard.py` — 24 теста.

### Часть 2. Этап 7 — CRUD шаблонов: ЧАСТИЧНО

Реализовано (эти части Этапа 7 в коде есть):
1. **Копирование checklist в задание по шаблону** — `create_task` services.py:5037–5041 (`DesignTaskChecklistItem.objects.bulk_create` из `template.checklist`); тест `test_create_task_with_template` (test_design_tasks.py:157–180) — проверяет копию, срок из default_duration_days и что шаблон остаётся с исходным списком (копия, не ссылка).
2. **Выбор шаблона при создании задания** — в форме: `task_templates` = активные шаблоны компании проекта (views.py:1422–1425), валидация принадлежности компании (:1519–1526), автозаполнение JS (task_form.html:117–131, 152–180).
3. **Админка** — `TaskTemplateAdmin` admin.py:116–123 (list_display/list_filter/search_fields). Спека §6/Этап 7 допускает «в админке **или** отдельной вкладке».

НЕ реализовано:
- Пользовательский CRUD «Настройки → Шаблоны заданий»: **нет маршрутов** (в `storage/adapters/urls.py` отсутствуют любые task_template-пути), **нет вьюх** (TaskTemplate в views.py упоминается только как источник данных формы: строки 1422, 1508), **нет шаблонов страниц** управления (в `storage/adapters/templates/storage/` нет template-файлов; TaskTemplate в шаблонах — только `<option>` в task_form.html).
- Редактирование пунктов чек-листа шаблона из UI — отсутствует (только через админку, JSONField).
- Специализированные тесты CRUD шаблонов — отсутствуют (покрытие шаблонов — через тесты create_task: :157–185).

Итог: «Настройки → Шаблоны заданий» как UI-раздел не существует; Этап 7 закрыт наполовину (механика есть, интерфейса нет).

---

## Часть 3. Встречный запрос задания (REQUESTED): НЕТ

Подтверждено отсутствием, пруфы:
- `DesignTask.Status` (models.py:707–716) — ровно 9 статусов спеки, REQUESTED отсутствует.
- Матрица переходов `_DESIGN_TASK_TRANSITIONS` (services.py:5086–5095) — 8 переходов, механика «смежник просит задание у источника» не представлена.
- Grep `REQUESTED|встречн|counter.request|counter_request` по *.py/*.html/*.js/*.css (без миграций) — все совпадения ложные:
  - `X-Requested-With` (AJAX-заголовки: static/js/pdf_viewer.js:984/1369/1665, app.js:867/1100, palette.js:90, docs/audit/…);
  - `test_token_requested_once_and_cached` (ai_checks/tests/test_gigachat.py:97);
  - локальная переменная `requested` в разборе GET-параметров списка документов (views.py:316–343);
  - имя теста `test_tasks_tab_all_requested_by_developer_falls_back` (test_design_tasks_views.py:176) — про role_filter, не про статус.
- Ближайшая по смыслу сущность — `RfiTask` (models.py:950+) — запросы **Заказчику**, отдельная сущность «не флаг на DesignTask (решение D1)» (models.py:951–953); к встречным запросам между смежными разделами отношения не имеет.

---

## Часть 4. Сверка docs/PROGRESS.md, CONTEXT.md, README.md с кодом

### 4.1. Главное расхождение: Этап 6 «ожидает применения» — ложь на HEAD

**Док** (docs/PROGRESS.md:6, обновлён 18.11.2026): «Модуль «Задания»: Этапы 1–5 закрыты (APPROVE)… Этап 6 (дашборд ГИПа, спека 4.4) — патч docs/audit/design-task-stage6/design-task-stage6.patch на базе 7fd3f92 готов, **ожидает применения Кодером и аудита**».
**Док** (docs/PROGRESS.md:353–357): чек-лист «Модуль «Задания» (Этап 6 — патч готов)»: «[ ] Этап 6 (текущий)… ожидает применения».
**Док** (docs/RFI_MODULE_PLAN.md:7, 16.11.2026): «База: main, HEAD = f0514da (**Этап 6 закрыт**: «Сводка по заданиям смежников», Excel-экспорт, UI-API)».
**Код** (HEAD ed8c2b1): Этап 6 полностью в коде — TaskDashboardView/ExportView/ApiView (views.py:1869/1974/2124), маршруты (urls.py:64/69/74), task_dashboard.html, сервисы (services.py:5351/5374), 24 теста.
**Git** (истина):
- `cfe5b1b` 15.11.2026 22:31 «feat(tasks): stage 6 dashboard + fix pre-existing test failures» — применение патча;
- `f0514da` 15.11.2026 22:55 «feat(dashboard): polish UI (rename to Summary), style Excel, add API for GIP-Assistant» — полировка (переименование в «Сводка», стили Excel, TaskDashboardApiView);
- база патча 7fd3f92 (15.11.2026 21:22, «feat(email): integrate SMTP Timeweb») существует в истории.
**Вывод:** прав RFI_MODULE_PLAN.md и владелец («Сводка работает») — Этап 6 применён 15.11.2026 и живёт в main; шапка PROGRESS.md и пункт :357 устарели (не переписаны после применения). Сам патч-файл `docs/audit/design-task-stage6/design-task-stage6.patch` и отчёт `REPORT.md` остаются в репозитории (артефакты, не признак «не применён»).

### 4.2. Полный список «док говорит X, код показывает Y»

| № | Документ | Док утверждает | Код показывает (пруф) |
|---|---|---|---|
| 1 | PROGRESS.md:6, :357 | Этап 6 ожидает применения Кодером и аудита | Применён: коммиты cfe5b1b (22:31) + f0514da (22:55) 15.11.2026; код в HEAD (views.py:1869/1974/2124; urls.py:64/69/74) |
| 2 | CONTEXT.md:19 | «Модуль «Задания»… карточка — Этап 4 в работе» | Карточка готова и закрыта: коммит d94e09f (15.11) «stage 4 task card» + afae6b8 «apply stage 4 audit patch»; код views.py:1767, task_detail.html |
| 3 | CONTEXT.md:21–22 | «Что делаем прямо сейчас? Этап 3 закрыт, работаем над Этапом 4» | Закрыты уже Этапы 4 (d94e09f/afae6b8), 5 (d699413), 6 (cfe5b1b/f0514da), затем модуль RFI R1–R3 (4c04e2c, e8d0ab1, 7d7e428), S3-sync A–D (c561f02, f52d4e1, 5d11da4, 825d158), фиксы вкладок/уведомлений (d50003c, d176eab, 0e3e43c), пин-подсветка (ddd9711) |
| 4 | README.md:34 | «…карточка задания — **в работе**» | Карточка реализована (см. №2); после неё в прод ушли Сводка, уведомления-напоминания, RFI, S3-экспорт |
| 5 | PROGRESS.md:358 | «[ ] Этап 7: шаблоны и чек-листы (CRUD)» | Верно, но неполно: копирование чек-листа и выбор шаблона в форме уже работают (services.py:5037–5041; task_form.html:117–131) — не реализован только UI-CRUD |
| 6 | PROGRESS.md:359 | «[ ] Этап 8: интеграция с документами и RAG» | Частично неверно: блок «Связанные задания» в карточке документа уже есть (document_detail.html:452–458; views.py:3985–3997, включая защиту share-ссылок :3979); не сделан только RAG-источник по заданиям (DesignTask не упоминается в ai_checks/) |
| 7 | docs/audit/design-task-stage6/REPORT.md:24 | кнопка входа «Дашборд» в тулбаре вкладки | Сейчас подписана «Сводка» (project_detail.html:507; переименовано в f0514da «rename to Summary») — отчёт устарел на одно слово |

CONTEXT.md датирован 29.10.2026 — старше всех перечисленных коммитов модуля (13–15.11), что и объясняет отставание.

---

## Часть 5. Разведка для «Матрицы заданий» (внутренняя, между разделами)

### 5.1. Есть ли матрица source_section × target_section в Stream-CAD: НЕТ

Grep `matrix|матриц` по *.py, *.html, *.js, *.css (без миграций/минифицированных либ) — все совпадения это:
- матрицы переходов статусов: DesignTask (services.py:5086–5095, комментарии views.py:1690–1707, urls.py:118–121), RfiTask (services.py, тесты test_rfi_services.py), FSM документов;
- матрица ролей зон ISO 19650 (`_notify_zone_transition`, services.py:3199+ и комментарии :734-контекст);
- матрица прав официального ответа на замечание (services.py, функции `change_issue_status`-семейства);
- `IDENTITY_MATRIX` и пр. внутри pdf.min.js (библиотечный код, нерелевантен).

Вьюх, шаблонов или сервисов с **группировкой DesignTask по парам разделов** не существует: ни один QuerySet кода не группирует по `(source_section, target_section)`. Grep `cross` по *.py — только ложные (`across_companies` в тестах, `CrossSourceRagTests`). Ближайшие к матрице данные — сериализация для внешней интеграции: `serialize_design_task` отдаёт `section_code: task.source_section.code` и `to_section_code: task.target_section.code` (storage/domain/sync/mapping.py:144–145) — т.е. **пары уже экспортируются наружу**, но внутри системы никак не агрегируются.

### 5.2. Правила матрицы ГИП-Ассистента — дословно по коду

Файлы (свежий клон 366ab96): `backend/src/domain/services/matrix_service.py` (169 строк), `backend/src/application/dto/cross_task.py` (35), `backend/src/domain/value_objects/enums.py` (CrossTaskStatus :26–33), `backend/src/adapters/api/routes/matrix.py` (113), `backend/src/adapters/api/routes/tasks.py` (drill-down :83+), `backend/src/adapters/db/repositories/task_repo.py` (get_by_pair :58+), `backend/src/adapters/db/models/cross_task.py`, фронт: `frontend/src/components/MatrixTable.tsx`, `MatrixCell.tsx`, `frontend/src/pages/MatrixPage.tsx`, `CrossTaskTasksPage.tsx`, `frontend/src/api/matrixApi.ts`.

**Примечание к запросу:** пользователь ожидал файл `backend/src/domain/services/matrix.py` — фактически сервис называется `matrix_service.py`; сущность `CrossTask` — `backend/src/domain/entities/cross_task.py`; всё остальное совпадает.

**5.2.1. Статусы ячейки (CrossTaskStatus)** — enums.py:26–33, дословно:

```python
class CrossTaskStatus(StrEnum):
    """Статус задания смежнику (ячейка матрицы)."""

    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING_INFO = "WAITING_INFO"
    DONE = "DONE"
    OVERDUE = "OVERDUE"
```

(Ждать в ячейке REWORK нельзя: REWORK — член **TaskStatus** (enums.py:21), не CrossTaskStatus.)

**5.2.2. Правила вычисления статуса ячейки** — `matrix_service.py:138–169`, дословно (порядок = приоритет):

```python
def _aggregate_status(tasks: list[Task]) -> CrossTaskStatus:
    """Агрегировать статус ячейки по статусам задач.

    Приоритет:
      1. Если все DONE → DONE
      2. Если есть REWORK → REWORK
      3. Если есть OVERDUE → OVERDUE
      4. Если есть IN_PROGRESS → IN_PROGRESS
      5. Иначе → NOT_STARTED
    """
    if not tasks:
        return CrossTaskStatus.NOT_STARTED

    statuses = [str(t.status) for t in tasks]

    # Все готовы
    if all(s == "DONE" for s in statuses):
        return CrossTaskStatus.DONE

    # Есть доработка
    if any(s == "REWORK" for s in statuses):
        return CrossTaskStatus.REWORK

    # Есть просрочка
    if any(_is_overdue(t) for t in tasks):
        return CrossTaskStatus.OVERDUE

    # Есть в работе
    if any(s == "IN_PROGRESS" for s in statuses):
        return CrossTaskStatus.IN_PROGRESS

    return CrossTaskStatus.NOT_STARTED
```

Ответы на вопросы запроса:
- **Порядок приоритетов:** REWORK (доработка) важнее просрочки; OVERDUE важнее IN_PROGRESS (просрочка проверяется раньше «в работе»); если ни одно условие не сработало — NOT_STARTED.
- **Что даёт WAITING_INFO:** ничего. WAITING_INFO объявлен в enum (enums.py:31) и присутствует в API-типах/лейблах/цветах фронтенда (matrixApi.ts:31 `WAITING_INFO`, :43 `"Ждёт информации"`, :49 жёлтый цвет), но **ни одна ветка их кода не возвращает этот статус** — grep WAITING_INFO по backend даёт только enums.py:31. Мёртвый член enum.
- **Два дефекта их реализации (факт, важен при переносе):**
  1. `matrix_service.py:158–159` — `return CrossTaskStatus.REWORK`: у CrossTaskStatus нет члена REWORK → AttributeError в рантайме, если в паре есть задача в статусе REWORK. Тестами не покрыто (`backend/tests/test_matrix_service.py` — 9 тестов: two_sections, two_directions, three_sections, aggregates, skips_diagonal, skips_missing_to_section, one_section, no_sections, idempotent — ни одного кейса REWORK/WAITING_INFO/OVERDUE).
  2. Задачи в статусе REVIEW (на проверке) в агрегации не учтены ни одной веткой: не DONE, не REWORK, не overdue (если срок не прошёл), не IN_PROGRESS → проваливаются в fallback NOT_STARTED.
- **Определение просрочки** — `_is_overdue` (matrix_service.py:129–135), дословно:

```python
def _is_overdue(task: Task) -> bool:
    """Просрочена ли задача."""
    if not task.deadline:
        return False
    if task.status in ("DONE", "CANCELLED"):
        return False
    return task.deadline < date.today()
```

**5.2.3. Как строятся оси** — фронтенд, `MatrixTable.tsx:16–23`, дословно:

```tsx
// Собираем все разделы из cross_tasks
const sections = useMemo(() => {
  const set = new Set<string>()
  crossTasks.forEach((ct) => {
    set.add(ct.from_section_code)
    set.add(ct.to_section_code)
  })
  return Array.from(set).sort()
}, [crossTasks])
```

- Оси — **только разделы, встречающиеся в ячейках** (Set из from+to), сортировка лексикографическая `Array.sort()`; бэкенд список разделов для осей не отдаёт.
- **Диагональ** `from === to` — пустая серая ячейка с `title="Диагональ"` (MatrixTable.tsx:69–78); ячейки без CrossTask — «—» (MatrixCell.tsx:18).
- Ячейки создаются **только по фактическим парам** задач: `generate_matrix` (matrix_service.py:37–123) — «Матрица строится по ФАКТИЧЕСКИМ парам (from_section → to_section) из задач проекта. Пустые пары — не создаются» (matrix_service.py:5–8); пустые поля и диагональ пропускаются (:68–71); задачи загружаются одним запросом лимитом 5000 (:57); пары индексируются на фронте как `from|to` (MatrixTable.tsx:26–32).
- **Персистентность:** CrossTask — отдельная таблица `cross_tasks` с уникальностью `(project_id, from_section_code, to_section_code)` (`uq_cross_task_project_sections`, db/models/cross_task.py:16–23); генерация идемпотентна — существующая ячейка обновляется, новая создаётся (matrix_service.py:96–114); перегенерация — по кнопке «Сгенерировать матрицу» (MatrixPage.tsx:32–34, POST `/api/projects/{id}/matrix/generate`).

**5.2.4. Что несёт ячейка** — статус + строка-описание со счётчиками: `description = f"Задач: {total}, просрочено: {overdue_count}, готово: {done}"` (matrix_service.py:92–94). Ячейка хранит только status и description (db/models/cross_task.py; CrossTaskRead dto cross_task.py:26–35: id, project_id, from/to, status, description, created_at, updated_at). На рендере ячейка показывает цветную точку «●» + **только общее число задач**, извлечённое регэксом из description: `description.match(/Задач:\s*(\d+)/)` (MatrixCell.tsx:24–28); просрочено/готово в ячейке не отображаются (только в drill-down).

**5.2.5. Drill-down** — есть, двухуровневый:
- API: GET `/api/projects/{id}/matrix` (список ячеек, routes/matrix.py:26–37), POST `.../matrix/generate` (:40–69), GET/PATCH `/api/cross-tasks/{id}` (:72–112), GET `/api/cross-tasks/{id}/tasks` — все задачи пары с вычисленными `overdue_days`/`days_until_deadline` (routes/tasks.py:83–120; выборка `task_repo.get_by_pair` по section_code+to_section_code, сортировка «сначала просроченные по возрастанию дедлайна, потом без дедлайна, потом остальные» — task_repo.py:58–76).
- UI: клик по ячейке → `/projects/{id}/matrix/{crossTaskId}` (MatrixPage.tsx:22–30) → страница «Задачи пары» (CrossTaskTasksPage.tsx): задачи сгруппированы в 5 сворачиваемых карточек-групп — «Просрочено» (открыта), «Скоро дедлайн (≤7 дней)» (открыта), «В работе», «Не начато», «Готово / Отменено» (:55–126); в каждой строке — название, срок, просрочка/остаток дней и **inline Select смены статуса задачи** (useUpdateTask, :180–191, :246–274). Плюс ручной PATCH самой ячейки (CrossTaskUpdate, dto :19–24).

**Итого модель ГИП-Ассистента:** ячейка = персистентный агрегат пары с цветным статусом и счётчиком задач; генерация по кнопке; drill-down с группировкой и правкой статусов; оси из фактических ячеек; WAITING_INFO — мёртвый статус; REWORK-ветка — падение в рантайме.

### 5.3. Сверка применимости к Stream-CAD: таблица соответствия

Наши статусы DesignTask (models.py:707–716): DRAFT, ISSUED, IN_PROGRESS, ON_HOLD, COMPLETED, REVIEW, ACCEPTED, RETURNED, CANCELLED.
Существующий официальный мост статусов уже есть в коде — `TASK_STATUS_MAP` (storage/domain/sync/mapping.py:22–33), используется внешним S3-экспортом tasks.json (sync/export.py:65):

| DesignTask → TASK_STATUS_MAP (наш код) | GA TaskStatus |
|---|---|
| DRAFT | NOT_STARTED |
| ISSUED | NOT_STARTED |
| IN_PROGRESS | IN_PROGRESS |
| ON_HOLD | NOT_STARTED |
| REVIEW | REVIEW |
| RETURNED | REWORK |
| COMPLETED | DONE |
| ACCEPTED | DONE |
| CANCELLED | CANCELLED |

Таблица соответствия «статус ячейки их матрицы — наши статусы — условие по нашим данным»:

| Их статус ячейки | У них (правило matrix_service) | Наши статусы задач пары через TASK_STATUS_MAP | Условие по нашим данным (source_section, target_section, status, due_date) | Переносимость |
|---|---|---|---|---|
| DONE | все задачи пары DONE | COMPLETED, ACCEPTED (оба → DONE) | все задачи пары (source→target) в {COMPLETED, ACCEPTED} | **Ложится 1:1**, но теряется различие «принято/на проверке»: у нас ACCEPTED — финал, COMPLETED — ещё не проверено; в их семантике оба «готово» |
| NOT_STARTED | есть задачи, но ни одна не DONE/REWORK/overdue/IN_PROGRESS | DRAFT, ISSUED, ON_HOLD (все → NOT_STARTED) | все задачи пары в {DRAFT, ISSUED, ON_HOLD} | **Ложится 1:1** |
| IN_PROGRESS | есть IN_PROGRESS, нет REWORK, нет overdue | IN_PROGRESS | хотя бы одна IN_PROGRESS, ни одна RETURNED, ни одна с due_date < сегодня (не-терминальный статус) | **Ложится 1:1** |
| OVERDUE | есть задача: deadline < today и статус не DONE/CANCELLED | — (время, не статус) | хотя бы одна задача пары: due_date < today и статус в не-терминальных | **Требует адаптации списка терминальных.** У них закрывает просрочку {DONE, CANCELLED}; у нас аналоги — {ACCEPTED, CANCELLED}, но наша Сводка считает просрочку по `_NOT_TERMINAL_TASK_STATUSES` = всё кроме {ACCEPTED, CANCELLED} (services.py:5340–5348), т.е. COMPLETED и REVIEW у нас просрочку **не гасят**, а у них DONE — гасит. Нужно решение Координатора: чья семантика |
| REWORK | есть задача REWORK (в коде падает: CrossTaskStatus.REWORK не существует) | RETURNED | хотя бы одна RETURNED в паре | **Логика переносима, их реализация — нет** (у нас RETURNED валиден; при переносе правила «REWORK → ячейка» надо ставить выше OVERDUE и не наследовать их баг) |
| WAITING_INFO | не производится нигде | ON_HOLD → TASK_STATUS_MAP даёт NOT_STARTED | — | **Не переносим без решения.** Ближайший наш смысл — ON_HOLD («приостановлено»), но их OWN-конвейер его не производит и их карта фронтенда ожидает. Если ячейке нужен «Ждёт информации», условие по нашим данным: все задачи пары в {ON_HOLD} (или хотя бы одна) — правила нет ни у них, ни у нас |
| REVIEW (их статус задачи, в ячейку не попадает) | проваливается в NOT_STARTED (fallback) | REVIEW | хотя бы одна REVIEW, остальное как NOT_STARTED | **Их пробел.** У нас REVIEW — массовый статус процесса проверки; для нашей матрицы требуется явное решение (отдельный статус ячейки «На проверке» или вливание в IN_PROGRESS/DONE) |

Дополнительно (факты, влияют на перенос):
- У них статус задачи CANCELLED в агрегации ячейки не участвует ни одной веткой: пара из не-DONE задач, где часть CANCELLED, даст NOT_STARTED/OVERDUE по остальным; у нас CANCELLED — терминальный, аналогично требует решения (исключать ли из агрегата).
- У них пара строится по строкам section_code/to_section_code задач (задача сама несёт пару); у нас пара — FK source_section/target_section, всегда заполнены и валидны (PROTECT), плюс «диагональ» у нас технически возможна (одно задание АР→АР не запрещено моделью/сервисом: create_task проверяет только принадлежность проекту, services.py:5008–5011) — при переносе правила «from != to» диагональные задания нужно либо исключать, либо запретить ранее.

### 5.4. Что уже готово в Stream-CAD для построения матрицы из наших данных

| Компонент | Где (файл:строка) | Что даёт матрице |
|---|---|---|
| Разделы с уникальным кодом в проекте | models.py:68–82 (`unique_together ('project','code')`) | оси матрицы: `Section.objects.filter(project=…)` |
| Сервисный COMMON | создаётся `_get_or_create_common_section` services.py:784–794; из UI исключается: `get_sections_with_counts` :548–556 (`.exclude(code='COMMON')`), форма задания views.py:1396–1400 (`.exclude(code='COMMON')`) | оси должны исключать COMMON тем же способом (прецедент в двух местах) |
| Пары разделов задания | models.py:732–743 — FK source_section/target_section, PROTECT, not-null | ячейка матрицы = группировка DesignTask по (source, target) — данных достаточно |
| Выборка заданий проекта | `list_tasks(project, user, 'all')` services.py:5454–5483 (гейт ГИП/админ, select_related source/target/author/assignee/template) | готовый материал агрегации с проверкой прав |
| Готовые агрегаты | `task_summary_for_gip` services.py:5351–5371; `tasks_requiring_attention` :5374–5438 (приоритет причин overdue→blocked→review→on_hold, декор is_overdue/overdue_days/attention_reason :5430–5432); константа `_NOT_TERMINAL_TASK_STATUSES` :5340–5348 | переиспользуемая семантика «просрочено/терминальный» и образец декора строк |
| Просрочка | is_overdue логика: views.py:1218–1224 (вкладка), services.py:5417–5421 (внимание) | условие OVERDUE-ячейки |
| Мост статусов | `TASK_STATUS_MAP` mapping.py:22–33 + `map_task_status` :69–73 | наши → GA-статусы для совместимости с их матрицей/фронтендом |
| Сериализация пары | `serialize_design_task` mapping.py:130–164 (`section_code` :144, `to_section_code` :145, `overdue_days`/`days_until_deadline` через `compute_deadline_metrics` :116–124) | формат задач пары для drill-down уже отработан |
| Каналы выдачи данных | внешний API `GET /api/v1/projects/<code>/tasks/` external_api/views.py:449–477 (token+scope TASKS_READ, `ApiTaskListView`, summary + tasks); S3-экспорт tasks.json (sync/export.py:65, стейдж B — коммит f52d4e1) | ГИП-Ассистент уже строит свою матрицу из наших заданий — данные согласованы |
| Свободные точки интеграции UI | список вкладок views.py:806 (кортеж вкладок) и `allowed_tabs` :847; guard-цепочка project_detail.html:486–647 (образец новой ветки `{% elif active_tab == ... %}`); маршруты urls.py:50–121 (задачный блок); кнопка «Сводка» в тулбаре вкладки Задания project_detail.html:506–510 (прецедент второй кнопки-входа); блок RFI на Сводке task_dashboard.html:140–159 (прецедент второго блока-агрегата) | куда встраивать матрицу: вкладка проекта или отдельная страница по образцу task_dashboard |
| Drill-down к паре (частичный) | фильтр вкладки `?tab=tasks&task_section=<id>` views.py:1209–1213 (по источнику **или** цели) | готовый приём, но фильтрует по одному разделу, не по паре (source AND target) — для drill-down пары нужен фильтр по двум |

Чего не хватает (факты): сервиса группировки по парам (нет ни одного QuerySet с group by (source_section, target_section)); сущности/модели ячейки-агрегата (в Stream-CAD нет аналога CrossTask; вопрос «persist vs вычислять на лету» — решение Координатора, в этом анализе не проектируется); UI-страницы/вкладки матрицы; маршрута; правила агрегации для REVIEW/RETURNED/ON_HOLD/COMPLETED (см. 5.3).

---

## Часть 6. Устарелость документации (списком)

| Файл | Что утверждает сейчас | Что утверждать по факту кода (HEAD ed8c2b1) |
|---|---|---|
| docs/PROGRESS.md:6 (шапка, 18.11.2026) | Этапы 1–5 закрыты; Этап 6 — патч готов, ожидает применения Кодером и аудита | Этапы 1–6 закрыты и применены (Этап 6 — коммиты cfe5b1b + f0514da 15.11.2026); следующий незакрытый — Этап 7 (частично: без UI-CRUD) и Этап 8 (частично: блок в карточке документа есть, RAG-источника нет) |
| docs/PROGRESS.md:353–359 (бэклог) | [ ] Этап 6 ожидает применения; [ ] Этап 7; [ ] Этап 8 | [x] Этап 6; Этап 7 — «частично (механика копирования есть, UI-CRUD нет)»; Этап 8 — «частично (блок «Связанные задания» есть, RAG нет)» |
| docs/CONTEXT.md:19 (29.10.2026) | «Модуль «Задания»… карточка — Этап 4 в работе» | Карточка (Этап 4) закрыта 15.11.2026 (d94e09f + afae6b8); закрыты также Этапы 5, 6; после модуля — RFI R1–R3, S3-sync A–D, пин-подсветка |
| docs/CONTEXT.md:21–22 | «Что делаем прямо сейчас? Этап 3 закрыт… работаем над Этапом 4» | Актуальная «точка сейчас» — после Этапа 6/R3/S3-D/пин-подсветки (последний код-коммит ddd9711 18.11) |
| README.md:34 | «Задания (DesignTask): … карточка задания — в работе» | Карточка реализована; в описании модуля также отсутствуют Сводка («Сводка по заданиям смежников» + Excel + API), напоминания Celery, RFI-вкладка — README описывает состояние на ~14.11 |
| docs/audit/design-task-stage6/REPORT.md:24 | кнопка «Дашборд» в тулбаре вкладки | Кнопка переименована в «Сводка» (f0514da; project_detail.html:507) |

Не устарели: docs/DESIGN_TASKS_SPEC.md (спека, сверяется с кодом), docs/RFI_MODULE_PLAN.md:7 (Этап 6 закрыт на f0514da — соответствует git log).

---

## Часть 7. Итог: готовность к следующим двум этапам

### 7.а. «Матрица заданий» (внутренняя, разделы проекта)

Данные и точки интеграции — **готовы**:
- Все исходные данные в моделях: пары (source_section, target_section) не-null и PROTECT на каждом DesignTask (models.py:732–743); оси — Section с уникальным (project, code) (models.py:81–82), с готовым прецедентом исключения COMMON (services.py:548–556, views.py:1396–1400); сроки/просрочка — due_date + отработанная семантика не-терминальных статусов (services.py:5340–5348, :5417–5421).
- Выборка с правами: list_tasks('all') (services.py:5454) с select_related — единственный источник, который нужен агрегатору; готовые агрегаты-образцы: task_summary_for_gip (:5351), tasks_requiring_attention (:5374).
- UI-точки свободны и с прецедентами: новая вкладка в guard-цепочке project_detail.html:486–647 + регистрация в views.py:806/:847; либо отдельная страница по образцу task_dashboard (urls.py:64–76, кнопка-вход в тулбаре вкладки Задания project_detail.html:506–510); drill-down — по образцу существующей вкладки ?tab=tasks (готовый приём фильтра, views.py:1209–1213).
- Внешняя совместимость: мост статусов TASK_STATUS_MAP (mapping.py:22) и сериализация пар (mapping.py:144–145) уже существуют — формат ячейки можно держать совместимым с их CrossTaskStatus.

Чего не хватает (факты): сервиса агрегации по парам (отсутствует), UI (отсутствует), маршрута (отсутствует), и — до старта — решений Координатора по четырём семантическим развилкам из 5.3: (1) REVIEW у нас массовый, у них в агрегации проваливается в NOT_STARTED; (2) RETURNED у нас валидный, у них ветка REWORK падает в рантайме; (3) гашение просрочки: у нас COMPLETED/REVIEW просрочку не гасят, у них DONE гасит; (4) ON_HOLD ↔ WAITING_INFO (у нас ON_HOLD маппится в NOT_STARTED, их WAITING_INFO мёртв).

### 7.б. «Встречный запрос задания (REQUESTED)»

Подтверждено: REQUESTED не существует (Часть 3). Конфликты/точки касания при его введении (по факту кода, без предложений):
- `DesignTask.Status` models.py:707–716 — добавить значение;
- матрица переходов `_DESIGN_TASK_TRANSITIONS` services.py:5086–5095 — новые (old, new)-кортежи; ветка CANCELLED :5137–5143 и `_COMMENT_REQUIRED` :5107–5111 — пересмотреть условия;
- **жёсткая точка: `TASK_STATUS_MAP`/`map_task_status`** (mapping.py:22–33, :69–73) — `map_task_status` бросает `ValueError` на неизвестном статусе; потребитель — S3-экспорт tasks.json (`serialize_design_task` → `map_task_status`, sync/export.py:65 → mapping.py:134) и `stream_cad_status` в API-полезной нагрузке (mapping.py:159). Введение REQUESTED без расширения карты ломает S3-синк и внешний контракт ГИП-Ассистента (их TaskStatus тоже не знает REQUESTED — enums.py:17–23);
- `TASK_STATUS_BADGES` views.py:275–285 — новый бейдж (иначе `text-bg-secondary` по умолчанию :1226);
- `_task_card_flags` views.py:1690–1732 — новая кнопка-действие и её роль;
- сегменты/выборки list_tasks services.py:5479–5482: incoming = assignee, outgoing = author — встречный запрос (просит источник) не является ни тем, ни другим; can_view_all_tasks/can_issue_task (views.py:1341, services.py:5441) — гейты формы;
- Сводка: `task_summary_for_gip` services.py:5360–5364 — by_status подхватит новый статус автоматически; пять фиксированных карточек `_summary_cards` (views.py:1921–1930) и чипы `_status_chips` — потребуют расширения, если REQUESTED нужно считать отдельно; блок «Требуют внимания» :5393–5399 не включает новый статус по умолчанию;
- уведомления: `_notify_task` (services.py:4944) переиспользуем; напоминания `_OPEN_TASK_STATUSES`/`_NOT_CLOSED_TASK_STATUSES` (:5490/:5500) — решить, входит ли REQUESTED;
- карточка документа: блок «Связанные задания» по вложениям (views.py:3985–3997) — нечувствителен к статусу;
- UI-подсказки прав: строки вьюхи can_issue (:1225–1229) и заголовки вкладки — сценарий «смежник просит у источника» не укладывается в текущее «Выдать задание» (кнопка :500–503, форма task_create с гейтом `_can_issue_task` views.py:1341–1357, требующим write к разделу).

Конфликтов с реализованным «насильно» нет (REQUESTED добавляется аддитивно в выборки), но обязательных точек правки шесть: Status, матрица переходов, TASK_STATUS_MAP (+ их TaskStatus), бейджи, card_flags, сегменты выборки.

---

## Приложение А. Хронология модуля «Задания» по git log (для сверки с доками)

| Коммит | Дата | Суть |
|---|---|---|
| 2958ca7 | 13.11.2026 | Stage 1: 5 моделей + миграция 0031 |
| 18c1f3b | 14.11.2026 | Stage 2: 7 сервисов + матрица переходов + 28 тестов |
| cf0d67a | 15.11.2026 | docs: stage3 tasks module update docs |
| d94e09f | 15.11.2026 | stage 4 task card |
| afae6b8 | 15.11.2026 | chore(tasks): apply stage 4 audit patch |
| d699413 | 15.11.2026 | sections-sync + stage5 notifications |
| 7fd3f92 | 15.11.2026 21:22 | feat(email): integrate SMTP Timeweb (база патча Этапа 6) |
| cfe5b1b | 15.11.2026 22:31 | **stage 6 dashboard применён** |
| f0514da | 15.11.2026 22:55 | polish UI (rename to Summary), style Excel, add API |
| 4c04e2c / e8d0ab1 / 7d7e428 | 16.11.2026 | RFI: R1 модели/сервисы, R2 UI, R3 API |
| c561f02 / f52d4e1 / 5d11da4 / 825d158 | 16–17.11.2026 | S3-sync стейджи A–D (export tasks.json — f52d4e1) |
| d50003c / d176eab / 0e3e43c | 17–18.11.2026 | фиксы вкладок/уведомлений (задача 6) |
| ddd9711 | 18.11.2026 | пин-подсветка замечаний |
| ed8c2b1 | 18.11.2026 23:18 | docs(audit): sprint1 patch + README path (текущий HEAD) |

Отчёт не содержит предложений по коду — только фактическое состояние и пруфы.
