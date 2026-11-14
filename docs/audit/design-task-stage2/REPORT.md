# Отчёт аудита: Этап 2 — Сервисы (бизнес-логика) модуля «Задания»

**Дата:** 2026-11-13
**База патча:** `main`, HEAD `2958ca7` (Этап 1 применён)
**Спецификация:** `docs/DESIGN_TASKS_SPEC.md` v1.0, раздел 3
**Патч:** `design-task-stage2.patch` (применяется `git apply` на чистый `2958ca7`)

## 1. Что сделано

Реализованы 7 сервисов модуля «Задания» в `storage/domain/services.py`
(бизнес-логика — только там, правило «гексагон лайт») + тестовый файл с 28
синхронными Django-тестами. Существующие модели и код не изменены — только
добавление импортов DesignTask-моделей и `create_notification` в шапку файла
и новый блок сервисов в конце.

| Сервис | Реализация по спеке 3 |
|---|---|
| `create_task` | write-доступ к `source_section` через `check_write_permission`; ValueError при разделах чужого проекта; копия `template.checklist` в `DesignTaskChecklistItem`; `due_date = today + default_duration_days` если не задан; статус DRAFT; `transaction.atomic` |
| `issue_task` | только DRAFT (иначе ValueError); права author/gip/admin; ISSUED + `issued_at`; лог; Notification исполнителю «Вам выдано задание #N: title» |
| `update_task_status` | матрица переходов словарём `_DESIGN_TASK_TRANSITIONS`; недопустимый переход → ValidationError, нет прав → PermissionDenied; ACCEPTED пишет `accepted_at`+`closed_by`; лог на каждый переход; уведомления COMPLETED/RETURNED/ACCEPTED |
| `add_task_attachment` | права по kind: input — author/gip/admin, output — только assignee и только при IN_PROGRESS, comment — участники; требует document или file |
| `toggle_checklist_item` | только assignee/gip/admin; True → done_by+done_at, False → сброс обоих |
| `task_summary_for_gip` | `{'total','by_status','overdue','on_hold','awaiting_review'}`; overdue = due_date < today И статус не терминальный |
| `list_tasks` | incoming (assignee=user) / outgoing (author=user) / all (только gip/admin, иначе PermissionDenied); `_ensure_project_access`; `select_related` из спеки |

## 2. Изменённые файлы

| Файл | Тип | Что изменено |
|---|---|---|
| `storage/domain/services.py` | изменён | импорты (DesignTask-модели, `create_notification`) + ~460 строк: 7 сервисов, матрица, хелперы `_is_project_gip`/`_is_project_admin`/`_is_gip_or_admin`/`_is_task_participant`/`_notify_task`/`_log_status_change` |
| `storage/tests/test_design_tasks.py` | новый | 28 тестов в 7 классах |

## 3. Проверки

### 3.1. `manage.py check`

```
System check identified no issues (0 silenced).
```

### 3.2. `manage.py test storage.tests.test_design_tasks`

```
Ran 28 tests in 29.4s

OK
```

### 3.3. Список тестов (28)

**CreateTaskTests:** test_create_task_ok, test_create_task_wrong_section,
test_create_task_no_permission, test_create_task_with_template (чек-лист
копируется + срок из default_duration_days + копия не ссылка),
test_create_task_explicit_due_date_wins.

**IssueTaskTests:** test_issue_task_ok (статус, issued_at, лог,
уведомление assignee с url), test_issue_task_by_gip,
test_issue_task_wrong_status (ValueError), test_issue_task_no_permission.

**UpdateTaskStatusTests:** test_update_status_matrix_happy_path (полная
цепочка DRAFT→…→ACCEPTED + accepted_at + closed_by + 5 записей лога),
test_update_status_matrix_forbidden (DRAFT→ISSUED мимо issue_task, автор
не может ISSUED→IN_PROGRESS, пропуск статусов, неизвестный статус,
самопереход), test_update_status_completed_requires_output,
test_update_status_on_hold_requires_comment, test_update_status_returned_requires_comment
(+ уведомление assignee), test_update_status_cancelled (без комментария,
исполнителю нельзя, терминальные ACCEPTED/CANCELLED), test_update_status_permissions_per_transition
(ГИП проводит COMPLETED→REVIEW, исполнитель — нет).

**AddTaskAttachmentTests:** input от автора / запрет исполнителю, output
только в IN_PROGRESS и только от assignee, comment для всех участников
(автор, исполнитель, ГИП, админ) и запрет чужаку, требование document/file,
ValueError на неизвестный kind.

**ToggleChecklistItemTests:** исполнитель (вкл/выкл со сбросом done_*),
ГИП разрешён, чужак — PermissionDenied.

**TaskSummaryForGipTests:** total=4, overdue=1 (просроченное, но ACCEPTED
не считается), on_hold=1, awaiting_review=1, by_status.

**ListTasksTests:** incoming/outgoing/all, PermissionDenied на 'all' для
не-ГИПа, ValueError на неизвестный фильтр, test_list_tasks_select_related
(2 запроса на superuser: exists-проверка проекта + список, без N+1).

### 3.4. Регресс

`manage.py test storage.tests issues.tests external_api.tests` →
475 тестов, 4 падения — все предсуществующие, подтверждены на чистом
`2958ca7` без патча (см. п. 5.5): test_gui ×2, test_sprint8 (zone chip),
test_views (contractor documents). К Этапу 2 не относятся; тесты
`test_design_tasks` полностью зелёные.

## 4. Проверка «по смыслу» (для Координатора)

1. **Матрица переходов** реализована ровно по таблице спеки 3 (9 разрешённых
   переходов + «Любой → CANCELLED»); роли проверяются по колонке «Кто».
2. **Права**: write-доступ создателя к source_section (create_task),
   assignee-only переходы, author/gip приёмка/возврат/отмена, admin —
   глобальная роль.
3. **Лог и уведомления**: каждый переход пишется в DesignTaskStatusLog;
   уведомления через существующий `Notification` (helper
   `issues.notify.create_notification`), актор сам себе уведомлений не
   получает; url — канонический адрес карточки Этапа 3.
4. **Безопасность отклонений**: чужак не может ни создать, ни выдать, ни
   комментировать; исполнитель не может принять своё задание; output
   валиден только в IN_PROGRESS; отмена требует комментария.

## 5. Интерпретации и замечания аудита

### 5.1. Обязательный комментарий

Матрица (колонка «Условие») требует комментарий для IN_PROGRESS→ON_HOLD,
REVIEW→RETURNED и «Любой»→CANCELLED — сервис валидирует непустой comment
(ValidationError после strip). Без этого правило было бы неисполнимым.

### 5.2. «Любой → CANCELLED» и терминальные статусы

CANCELLED разрешён из всех статусов, кроме ACCEPTED (задание уже закрыто,
closed_by записан) и CANCELLED (повторная отмена — no-op). Строгое «любой»
включало бы отмену принятого задания — осознанное ограничение, отмечено в
docstring и комментариях.

### 5.3. Глаголы Notification

`Notification.VERB_CHOICES` не содержит «задачных» глаголов. Модель issues
не трогалась (расширение choices потребовало бы миграцию существующей
модели — за рамками Этапа 2), использованы ближайшие по смыслу: `assigned`
(выдача задания), `status_changed` (переходы). Текст сообщения несёт
конкретику («Вам выдано задание #N…»). Рекомендация: на Этапе 3 добавить
глаголы `task_issued`/`task_status_changed` отдельным патчем вместе с UI
бейджей, если понадобится различать в колокольчике.

### 5.4. Прочие решения

- `awaiting_review` — только статус REVIEW (не COMPLETED): COMPLETED ещё
  не подано на проверку автором (спека 4.4: «на проверке»).
- `add_task_attachment` требует хотя бы document или file (ValidationError)
  — вложение без содержимого бессмысленно; спека формулировки не задаёт.
- gip = `project.gip` или `ProjectMembership(role_in_project='gip')`
  (проектный уровень, как в `can_manage_project_participants`); admin =
  superuser или `UserProfile.role == 'admin'`.
- Сервисные события логируются `logger.info` (конвенция services.py).

### 5.5. Предсуществующие падения тестов на 2958ca7 (не в патче)

На чистом `2958ca7` падают 4 теста (не DesignTask):
`storage.tests.test_gui.GuiViewTests.test_corrupted_saved_view_config_falls_back`,
`test_foreign_users_view_is_not_applied`,
`storage.tests.test_sprint8.FilterBarTests.test_zone_chip_displayed`,
`storage.tests.test_views.ViewTests.test_project_detail_contractor_sees_only_allowed_documents`.
Симптом: контент «АР-01» отсутствует в ответе project_detail. К Этапу 2 не
относятся (патч не трогает вьюхи/шаблоны/виды). Рекомендую Координатору
поставить отдельное задание на разбор (похоже на регресс последних UI-правок).

## 6. Применение

```bash
git checkout main && git pull            # ожидается HEAD 2958ca7 (или позже)
git apply --check design-task-stage2.patch
git apply design-task-stage2.patch
python manage.py check
python manage.py test storage.tests.test_design_tasks
```
