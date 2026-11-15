# Аудит Этапа 5 — уведомления и напоминания (модуль «Задания»)

- **Роль:** Аудитор (Z.ai / GLM, режим Debug)
- **База:** `afae6b8` — `chore(tasks): apply stage 4 audit patch` (HEAD `origin/main`)
- **Дата:** 2026-11-15
- **Спецификация:** `docs/DESIGN_TASKS_SPEC.md`, Этап 5 («Уведомления и напоминания») + п. 3 (матрица переходов)
- **Артефакт:** `design-task-stage5.patch` (уведомления/сервисы + Celery-задачи + beat + тесты), `logs/` (демонстрация расписания)
- **Коммит:** не выполнялся — ожидает APPROVE Координатора

---

## 1. Что проверено

| Критерий приёмки (из промпта) | Метод проверки |
|---|---|
| Триггеры уведомлений при `issue_task` и всех переходах `update_task_status` | чтение `storage/domain/services.py`, прогон unit-тестов на каждый переход |
| Использование существующей `Notification`, без новых полей | чтение `issues/models.py`, `git diff` патча (модели/миграции не тронуты) |
| Ссылки `url` ведут на карточку задания | ассерты в тестах + проверка `_design_task_url` |
| Три Celery-задачи `notify_task_due_soon` / `due_today` / `overdue` | чтение `storage/tasks.py` (создан патчем), eager-прогон, мок `apply_async` |
| Расписание celery-beat, разумные интервалы | чтение `CELERY_BEAT_SCHEDULE`, тест расписания, лог `worker -B` |
| Идемпотентность напоминаний | двойной запуск сервиса в тестах и в демо |
| Тесты unit + Celery, `--noinput`, регресс без падений | запуск `storage.tests` + `issues` (553 теста) |
| Безопасность: уведомления не покидают проект | тест «получатель из другой компании» |
| Производительность: одна выборка заданий | `assertNumQueries(3)` + select_related/only |

## 2. Состояние базы `afae6b8` до патча

### 2.1. ОК (реализовано в Этапах 2–4, подтверждено)

1. **4 из 9 триггеров уведомлений** в сервисах: выдача (`issue_task` → assignee, verb `assigned`), `→COMPLETED` (→ автор/ГИП), `→RETURNED` (→ assignee), `→ACCEPTED` (→ assignee). Тексты содержат номер задания и ссылку.
2. **Существующая модель `Notification`** (`issues/models.py`) — новые поля не добавлялись; создание через `issues.notify.create_notification` (не шлёт актору, переживает ошибки).
3. **Ссылки** — `_design_task_url`: `/projects/<id>/tasks/<task_id>/`, совпадает с роутом `task_detail` (Этап 3).
4. **Уведомления атомарны** смене статуса: создаются внутри `transaction.atomic()` сервиса.

### 2.2. REWORK (выявлено аудитом, устранено патчем)

| # | Проблема | Фикс в патче |
|---|---|---|
| R1 | **5 триггеров отсутствуют**: `ISSUED→IN_PROGRESS` («принято в работу»), `IN_PROGRESS→ON_HOLD` («приостановлено. Причина: …»), `ON_HOLD→IN_PROGRESS` («возобновлено»), `COMPLETED→REVIEW` («отправлено на проверку»), `→CANCELLED` («отменено. Причина: …») | добавлены вызовы `_notify_task` в `update_task_status` (только вызовы создания Notification — матрица/права не тронуты) |
| R2 | **Текст не по спеке**: `IN_PROGRESS→COMPLETED` — «выполнено и ждёт проверки» вместо «ожидает проверки» | текст выровнен: `Задание #N ожидает проверки` |
| R3 | **Текст не по спеке**: `REVIEW→RETURNED` — «возвращено на доработку: …» без слова «Причина:» | `Задание #N возвращено на доработку. Причина: …` |
| R4 | **Celery-задачи отсутствуют полностью** (`storage/tasks.py` / `sod_core/tasks.py` не существовали) | создан `storage/tasks.py` — 3 задачи-обёртки над сервисами |
| R5 | **Расписание beat отсутствует** (удалено при зачистке ai_checks 2026-11-09 и не восстановлено) | `CELERY_BEAT_SCHEDULE` в `sod_core/settings.py` (+ `CELERY_TIMEZONE`) |
| R6 | **Идемпотентность напоминаний** — нечего было проверять (R4) | дедупликация (recipient, url, message) за сутки + `bulk_create` |
| R7 | **Проверка «получатель ∈ участники проекта»** отсутствовала: сервис `create_task` не валидирует принадлежность исполнителя проекту | guard `_is_project_participant` в `_notify_task` и в напоминаниях |

## 3. Матрица событий (после патча)

Получатель по спеке Этапа 5 указан дословно; «реализация» — фактические получатели патча (см. §4 об интерпретации). Все уведомления переходов создаются внутри транзакции `update_task_status`; ссылка у всех — карточка задания.

| Переход | Кто делает | Получатель (спека) | Получатель (реализация) | Текст уведомления | verb |
|---|---|---|---|---|---|
| DRAFT→ISSUED (`issue_task`) | автор/ГИП | assignee | assignee | `Вам выдано задание #N: {title}` | `assigned` |
| ISSUED→IN_PROGRESS | assignee | assignee | **автор + ГИП** | `Задание #N принято в работу` | `status_changed` |
| IN_PROGRESS→ON_HOLD | assignee | assignee | **автор + ГИП** | `Задание #N приостановлено. Причина: {comment[:200]}` | `status_changed` |
| ON_HOLD→IN_PROGRESS | assignee | assignee | **автор + ГИП** | `Задание #N возобновлено` | `status_changed` |
| IN_PROGRESS→COMPLETED | assignee | author/gip | автор + ГИП | `Задание #N ожидает проверки` | `status_changed` |
| COMPLETED→REVIEW | автор/ГИП | assignee | assignee | `Задание #N отправлено на проверку` | `status_changed` |
| REVIEW→RETURNED | автор/ГИП | assignee | assignee | `Задание #N возвращено на доработку. Причина: {comment[:200]}` | `status_changed` |
| REVIEW→ACCEPTED | автор/ГИП | assignee | assignee | `Задание #N принято: {title}` | `status_changed` |
| любой не-терминальный→CANCELLED | автор/ГИП | assignee/author | assignee + автор (без актора) | `Задание #N отменено. Причина: {comment[:200]}` | `status_changed` |

Актор всегда исключается из получателей (`create_notification` не шлёт актору; в групповых рассылках актор отфильтрован через `… - {None, user}`). Терминальные статусы (ACCEPTED, CANCELLED) отменять нельзя — матрица Этапа 2, не изменена.

### Напоминания (Celery, actor=None, verb=`status_changed`)

| Задача | Условие выборки | Статусы | Получатели | Текст |
|---|---|---|---|---|
| `notify_task_due_soon` | `due_date == сегодня+2` | ISSUED, IN_PROGRESS, ON_HOLD, REVIEW | assignee | `Задание #N «{title}»: срок через 2 дня (DD.MM.YYYY)` |
| `notify_task_due_today` | `due_date == сегодня` | те же | assignee | `Задание #N «{title}»: срок истекает сегодня (DD.MM.YYYY)` |
| `notify_task_overdue` | `due_date < сегодня` | всё, кроме COMPLETED/ACCEPTED/CANCELLED (вкл. DRAFT, RETURNED) | assignee + автор + ГИП | `Задание #N «{title}»: просрочено, срок был DD.MM.YYYY` |

### Расписание (`CELERY_BEAT_SCHEDULE`, `sod_core/settings.py`)

| Запись | Задача | Расписание | Примечание |
|---|---|---|---|
| `notify-task-due-soon` | `storage.tasks.notify_task_due_soon` | `crontab(hour=8, minute=0)` — раз в сутки | 08:00 по Москве (`CELERY_TIMEZONE = TIME_ZONE`) |
| `notify-task-due-today` | `storage.tasks.notify_task_due_today` | `crontab(hour=8, minute=15)` — раз в сутки | после due-soon |
| `notify-task-overdue` | `storage.tasks.notify_task_overdue` | `crontab(minute=0)` — раз в час | час:00; дубли в пределах суток режет идемпотентность |

## 4. Интерпретации (документированные решения)

1. **Получатели «assignee» для действий исполнителя (R1).** Спека дословно указывает получателем исполнителя для `ISSUED→IN_PROGRESS`, `IN_PROGRESS→ON_HOLD`, `ON_HOLD→IN_PROGRESS`, но актор — сам исполнитель, а `create_notification` не уведомляет актора о своём действии. Дословная реализация давала бы **ноль** уведомлений на эти переходы. Принято: получатель — противоположная сторона (автор + ГИП), тексты — дословно по спеке. Симметрия сохраняется: действия автора/ГИПа видит исполнитель (спека совпадает буквально). Отражено в матрице §3 и в докстринге `update_task_status`. Если Координатор хочет иначе — правка только блока уведомлений, логика не трогается.
2. **Просрочка включает DRAFT и RETURNED.** Спека: «если всё ещё не COMPLETED/ACCEPTED/CANCELLED» — взято дословно: черновик с прошедшим сроком и «возвращённое на доработку» тоже просрочены. Напоминания «срок скоро/сегодня» — только открытые статусы (ISSUED, IN_PROGRESS, ON_HOLD, REVIEW) — по спеке дословно.
3. **Получатели напоминаний.** Спека получателей не задаёт. Принято: «срок скоро/сегодня» — исполнитель; просрочка — исполнитель + автор + ГИП (просрочка важна инициатору и руководителю; стыкуется с «Требуют внимания» дашборда ГИПа, Этап 6).
4. **«Участник проекта» = граница компании.** Доступ к проектам в СОД определяется компанией (`get_accessible_projects`): участник проекта для уведомлений — пользователь компании проекта, ГИП проекта или superuser. Пользователь другой компании (или без профиля) не получает ничего — покрыто тестом. `ProjectMembership` дополнительно не проверяется, чтобы не расходиться с фактической моделью доступа UI.
5. **verb для напоминаний = `status_changed`.** `Notification.VERB_CHOICES` не содержит «deadline»-глаголов; расширение verb'ов = изменение модели issues, что запрещено спекой Этапа 5. Использован ближайший по смыслу глагол (как и в переходах Этапа 2). Иконка колокольчика — `bi-arrow-repeat`; текст однозначно отличает напоминание.

## 5. Что внесено патчем

**4 файла, ~750 строк; модели/миграции/вьюхи/шаблоны не изменяются.**

| Файл | Изменение |
|---|---|
| `storage/domain/services.py` | +import `Notification`, `datetime`; +`_is_project_participant` (guard); `_notify_task` проверяет получателя; блок уведомлений `update_task_status` — 9 переходов (5 добавлено, 2 текста выровнены); +константы `_OPEN_TASK_STATUSES`, `_NOT_CLOSED_TASK_STATUSES`; +`_deadline_tasks` (один запрос, select_related + only), `_reminder_recipients`, `_send_task_deadline_notifications` (дедуп за сутки + bulk_create); +3 публичных сервиса `notify_tasks_due_soon` / `due_today` / `overdue`. Матрица переходов, права и валидации **не изменены** |
| `storage/tasks.py` (новый) | 3 `@shared_task` с именами `storage.tasks.notify_*` — тонкие обёртки над сервисами |
| `sod_core/settings.py` | +`from celery.schedules import crontab`; +`CELERY_TIMEZONE`; +`CELERY_BEAT_SCHEDULE` (блок размещён после `TIME_ZONE` — crontab-часы считаются в зоне проекта) |
| `storage/tests/test_design_tasks_notify.py` (новый) | 26 тестов: 9 переходов + 2 безопасности + 10 напоминаний + 5 Celery/beat |

**Проверенные файлы (без изменений):** `issues/models.py` (Notification: recipient/actor/message/url/is_read/created_at), `issues/notify.py` (`create_notification`), `sod_core/celery.py` (`app.autodiscover_tasks()` находит `storage.tasks`; `app.set_default()` биндит shared_task к нашему приложению), `issues/context_processors.py`, `_topbar.html`, `_notifications_list.html` (колокольчик рендерит любые `Notification` по `message`+`url` — новые уведомления отображаются без правок шаблонов), `docs/DESIGN_TASKS_SPEC.md`.

## 6. Тест-план и результаты

### 6.1. Новые тесты (`storage/tests/test_design_tasks_notify.py`, 26)

| Класс | Тесты |
|---|---|
| `TaskStatusNotificationTests` (9) | по тесту на каждый переход: получатель, verb, текст (вкл. «Причина: …»), url-карточка, актор не получает своё действие |
| `TaskNotificationSecurityTests` (2) | получатель из другой компании не получает уведомления (ни при выдаче, ни при смене статуса); коллега той же компании получает (граница — компания) |
| `ReminderTasksTests` (10) | окно «+2 дня» точное (±1 день не попадает); DRAFT/COMPLETED не напоминаются; «сегодня»/просрочка; терминальные статусы исключены; RETURNED напоминается; получатели просрочки = 3; идемпотентность (повтор → 0, без дубликатов); `assertNumQueries(3)` — один запрос выборки |
| `CeleryTasksAndBeatTests` (5) | задачи зарегистрированы на приложении; в `CELERY_BEAT_SCHEDULE` все 3 записи; due_* — раз в сутки (hour={8}), overdue — раз в час (hour=0..23, minute={0}); `CELERY_TIMEZONE` = Europe/Moscow; `.delay()` в eager-режиме выполняется синхронно; мок `apply_async` подтверждает точку вызова beat |

### 6.2. Прогоны (все с `--noinput`)

| Что запускалось | Команда | Результат |
|---|---|---|
| Новые тесты | `manage.py test storage.tests.test_design_tasks_notify` | **26/26 OK** |
| Регресс | `manage.py test storage.tests issues` | **553/553 OK** (~9 мин) |
| Чистый клон | `git apply` на клон `afae6b8` → check/migrate/тесты | **141/141 OK** (`manage.py check` — no issues) |

Проверка `makemigrations --check` на чистом клоне выдаёт 2 предсуществующих label-only `AlterField` (`Document.suitability_code`, `Folder.zone`) — известная особенность базы до Этапа 1, к Этапу 5 отношения не имеет, патч миграций не содержит.

## 7. Логи celery — демонстрация расписания

Окружение аудита: внешний Redis недоступен (нет root для установки redis-server), брокер — `memory://` (Kombu, «Messages can be passed only between threads»), `CELERY_TASK_ALWAYS_EAGER=false`. Файлы в `logs/`.

**7.1. Прод-расписание, `celery -A sod_core worker -B -l info`** (`logs/prod_schedule.log`) — задачи зарегистрированы, beat стартовал по `CELERY_BEAT_SCHEDULE` из settings:

```
[tasks]
  . ai_checks.index_knowledge_document
  . ai_checks.index_project_document
  . ai_checks.reindex_project
  . storage.tasks.notify_task_due_soon
  . storage.tasks.notify_task_due_today
  . storage.tasks.notify_task_overdue

[... INFO/Beat] beat: Starting...
[... INFO/MainProcess] celery@prod-schedule-demo ready.
```

**7.2. Срабатывание расписания** (`logs/accelerated_beat.log`) — тот же worker -B с ускоренным расписанием (10 сек вместо crontab, имена задач продовые; модуль-обёртка — `logs/demo_celery.py`). Beat отправляет все три задачи:

```
[2026-11-15 12:47:19,558: INFO/Beat] Scheduler: Sending due task notify-task-overdue (storage.tasks.notify_task_overdue)
[2026-11-15 12:47:19,560: INFO/Beat] Scheduler: Sending due task notify-task-due-soon (storage.tasks.notify_task_due_soon)
[2026-11-15 12:47:19,561: INFO/Beat] Scheduler: Sending due task notify-task-due-today (storage.tasks.notify_task_due_today)
```

— повторяется каждые 10 секунд (15 отправок за прогон). Ограничение окружения: memory-транспорт не доставляет сообщения consumer'у воркера внутри процесса (виртуальный транспорт kombu), поэтому приём сообщений демонстрировался в eager-режиме; на проде с Redis (`CELERY_BROKER_URL=redis://…`, `CELERY_TASK_ALWAYS_EAGER=false`) транспорт — стандартный celery-redis, код задач не зависит от транспорта.

**7.3. Функциональный прогон задач** (`logs/eager_demo.log`) — реальные данные в dev-базе, задачи вызваны через Celery (eager):

```
Созданы задания #2 (просрочено), #3 (срок через 2 дня), #4 (срок сегодня); все ISSUED

1) notify_task_due_soon  -> создано уведомлений: 1
2) notify_task_due_today -> создано уведомлений: 1
3) notify_task_overdue   -> создано уведомлений: 3
Повторный запуск notify_task_overdue -> 0   (идемпотентность)

- получатель=demo_assignee… url=/projects/3/tasks/2/
    Задание #2 «Демо-задание: учесть нагрузки»: просрочено, срок был 14.11.2026
- получатель=demo_author…   url=/projects/3/tasks/2/   (аналогично)
- получатель=demo_gip…      url=/projects/3/tasks/2/   (аналогично)
- получатель=demo_assignee… url=/projects/3/tasks/4/
    Задание #4 «Демо-задание: пояснительная записка»: срок истекает сегодня (15.11.2026)
- получатель=demo_assignee… url=/projects/3/tasks/3/
    Задание #3 «Демо-задание: схемы опор»: срок через 2 дня (17.11.2026)
```

Лог уровня сервиса: `notify deadline batch: tasks=1 created=3`, затем `tasks=1 created=0`.

## 8. Применение патча

```bash
cd sod-project
git checkout afae6b8            # база Этапа 5
git apply --check docs/audit/design-task-stage5/design-task-stage5.patch
git apply docs/audit/design-task-stage5/design-task-stage5.patch
python manage.py test storage.tests.test_design_tasks_notify --noinput   # 26 OK
python manage.py test storage.tests issues --noinput                     # 553 OK
```

Патч затрагивает `services.py`/`settings.py` в местах, не пересекающихся с патчами Этапов 1–4 (проверено применением поверх afae6b8, в который эти патчи уже включены).

## 9. Вывод

| Направление | Вердикт |
|---|---|
| Триггеры уведомлений (9 переходов + выдача) | **OK после патча** (4 было, 5 добавлено, 2 текста выровнены) |
| Модель Notification без изменений | **OK** |
| Ссылки на карточку задания | **OK** (было и до патча) |
| Celery-задачи + beat-расписание | **OK после патча** (отсутствовали полностью) |
| Идемпотентность | **OK после патча** (дедуп за сутки + bulk_create) |
| Безопасность (recipient ∈ участники) | **OK после патча** (guard границы компании) |
| Производительность (одна выборка) | **OK после патча** (select_related + only, assertNumQueries(3)) |
| Регресс storage.tests + issues | **OK** (553/553) |

**Статус: REWORK устранён патчем; направляю на APPROVE.** Коммит не выполнялся. Открытые вопросы для Координатора: интерпретация получателей действий исполнителя (§4.1) и состав получателей просрочки (§4.3) — при другом решении правка ограничена блоком уведомлений сервиса.
