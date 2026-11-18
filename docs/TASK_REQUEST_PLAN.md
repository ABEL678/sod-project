# План: Обратный запрос задания (REQUESTED) — модуль DesignTask

**Автор:** Аудитор (Z.ai / GLM-5.3-Flash, режим Debug).
**Дата:** 2026-11-19.
**Режим:** только проектирование. Код не пишется, коммиты не делаются.
**База:** `github.com/ABEL678/sod-project`, ветка `main`, **HEAD = `ed8c2b1a395f36470073b591d7272e1616d3b7d4`** — тот же, что у `docs/MATRIX_TASKS_PLAN.md` (клон `/home/z/my-project/work/sod-project`).
**Источники фактов:** `docs/DESIGN_TASK_AUDIT.md` (Части 1–7, локальная копия, база совпадает с HEAD) + перекрёстная проверка кода на свежем клоне в этой сессии; `docs/MATRIX_TASKS_PLAN.md` (выпущен в этой же сессии: У8-а `:228`, Р6 `:296`, У5 `:225`, §4.6 `:207`).
**Скоуп:** механизм «смежник просит задание у источника» (КР → АР: «дай задание на отверстия»). **RFI (`RfiTask`, `models.py:950–956`, докстринг `models.py:953–954` — «Отдельная сущность, не флаг на DesignTask») в скоуп не входит** — запросы Заказчику, отдельная вертикаль. Сущности не смешивать.
**Правила репо:** бизнес-логика — только `services.py`; модели менять только с обновлением `ARCHITECTURE.md`; НЕТ UI — НЕТ ФИЧИ (User Flow в патче); патчи применяются Кодером на origin/main (Инструкция для Z.ai, правила 1–5).

---

## 1. Резюме (TL;DR)

REQUESTED — десятый статус жизненного цикла `DesignTask.Status` (`models.py:707–716`), а не новая сущность: представитель раздела-цели создаёт запись с пустым автором и `requested_by`, раздел-источник оформляет задание (accept) или отклоняет (decline). Аудит подтвердил: REQUESTED в коде не существует (аудит Часть 3), а ввод нового статуса имеет **одну жёсткую точку** — `TASK_STATUS_MAP` (`mapping.py:22–33`, `map_task_status` `:69–73` бросает ValueError; потребитель — S3-экспорт `sync/export.py:56–65`, который сериализует **все** задания проекта без фильтра статусов).

Ключевое **уточнение аудита** этой сессией: внешний API `/tasks/` **не падает** — у него собственный маппинг `GA_STATUS_MAP` (`external_api/serializers.py:11–21`) с безопасным fallback `.get(status, 'NOT_STARTED')` (`:54–55`); формулировка аудита §7.б «ломает S3-синк и внешний контракт» верна только наполовину (Ф2, §4.2). Второй маппинг существует и имеет **другой состав** (ON_HOLD→REWORK, COMPLETED→REVIEW против ON_HOLD→NOT_STARTED, COMPLETED→DONE в S3-карте) — расширять нужно оба, составы не унифицировать (это зафиксированные контракты).

Состав работ: одна миграция (поле `requested_by` + `author` null/blank + choices статуса), три сервиса (`request_task` / `accept_task_request` / `decline_task_request`) + guard в `update_task_status`, расширение сегментов `list_tasks` (REQUESTED автоматически не попадает ни во «Входящие», ни в «Исходящие» — пруфы §3.7), UI (кнопка → форма → карточка с баннером и тремя кнопками → Сводка «Запросы: N»), связка с матрицей через drill-down WAITING_INFO-ячеек. Матрица REQUESTED не ломает (whitelist-заготовка матричного плана У8-а/Р6).

### 1.1. Принятые решения Координатора (фиксируются как есть)

| № | Решение | Статус |
|---|---|---|
| К1 | Модель: НЕ новая сущность, а статус REQUESTED на существующем DesignTask (вариант «смещённая пара» из аудита). Запрашивает представитель target_section; author при создании пуст, заполняется источником при оформлении | Принято |
| К2 | Новое поле requested_by: FK User, null=True, PROTECT. Одна миграция. author → null=True (пруф `models.py:697+`; проверить, не сломает ли null существующие constraint'ы и формы) | Принято (проверка constraint'ов/форм — §3.1: не сломает) |
| К3 | Жизненный цикл: request_task (write на target; статус REQUESTED, requested_by=user, author=None; лог; уведомление write-пользователям source + ГИПу); accept_task_request (write на source или gip/admin; правит поля, author=user, статус ISSUED, issued_at; лог; уведомление requested_by); decline_task_request (comment обязателен; права: write на source, gip/admin или сам requested_by — отзыв; статус CANCELLED; лог); update_task_status НЕ переводит REQUESTED — расширить guard-цепочку | Принято |
| К4 | Жёсткий блокер: расширить TASK_STATUS_MAP (`mapping.py:22–33`) — REQUESTED → ? (предложить значение с обоснованием); пока не расширен — S3-экспорт падает на ValueError (`mapping.py:69–73`); проверить внешний API-сериализатор (есть ли отдельный маппинг кроме S3) | Принято (проверка выполнена: результат Ф2 — падает только S3; второй маппинг существует) |
| К5 | Видимость: REQUESTED виден источнику в «Исходящих» (его раздел — source), запрашивателю — в «Входящих» (его раздел — target). Проверить по коду, как считаются сегменты | Принято (проверка выполнена: автоматически НЕ попадает — §3.7) |
| К6 | UI (НЕТ UI — НЕТ ФИЧИ): кнопка «Запросить задание» → форма (?mode=request); карточка REQUESTED — баннер + «Оформить задание»/«Отклонить»/«Отозвать запрос»; бейдж по дизайн-токенам; Сводка — счётчик «Запросы: N», согласовать с матричным планом | Принято |
| К7 | Связка с матрицей: WAITING_INFO-ячейки — кандидаты на запросы; в drill-down ячейки — кнопка «Запросить задание» с предзаполненной парой; учесть заготовку У8-а матричного плана | Принято |
| К8 | Чек-лист из шаблона копируется при создании запроса (прецедент `create_task` `services.py:5037–5041`) | Принято |
| К9 | Тесты: точечный файл, покрытие по перечню (создание/negative/accept/decline/отзыв/экспорт/уведомления) | Принято |

### 1.2. Формализации Аудитора (уточняют решения, не меняют их сути)

| № | Формализация | Обоснование (пруфы) |
|---|---|---|
| Ф1 | `author` меняется на `null=True, blank=True` (К2 — только null); `requested_by` — `null=True, blank=True` | `blank=True` требуется для ModelForm/админки: без него форма потребует автора даже у REQUESTED (стандартное поведение Django ModelForm); прецедент nullable-FK в этой модели — `assignee` (null+blank, `models.py:751–757`) |
| Ф2 | Уточнение К4: на REQUESTED падает **только S3-экспорт** (`build_tasks_payload` берёт все задания проекта без фильтра статусов — `sync/export.py:56–65`, `map_task_status` `mapping.py:69–73`). Внешний API `/api/v1/projects/<code>/tasks/` не падает: собственный сериализатор `ApiTaskReadSerializer` (`external_api/serializers.py:30–52`) с собственным маппингом `GA_STATUS_MAP` (`:11–21`) и fallback `.get(task.status, 'NOT_STARTED')` (`:54–55`). Формулировка аудита §7.б «ломает S3-синк и внешний контракт» — верна наполовину | Пруфы в §4.1–4.2. Оспорение решения разрешено правилами задачи: с пруфами и альтернативой — альтернатива здесь «внешний API можно не трогать, но стоит добавить явный ключ для самодокументации» (§4.2) |
| Ф3 | Guard REQUESTED в `update_task_status` обязателен и ставится **до** CANCELLED-ветки: ветка «Любой → CANCELLED» (`services.py:5126–5131`) обходит матрицу переходов, и без guard ГИП/админ сможет отменять REQUESTED напрямую, минуя decline (с комментарием-причиной и уведомлением запрашивателю). Симметрично: флаг `can_cancel` в `_task_card_flags` (`views.py:1718–1721`) исключает REQUESTED | Пруфы: CANCELLED-ветка не проверяет `(old, new)` по `_DESIGN_TASK_TRANSITIONS`; REQUESTED не входит в `_DESIGN_TASK_FINAL_STATUSES` {ACCEPTED, CANCELLED} (`services.py:5101–5104`) |
| Ф4 | Получатели уведомлений при создании/отзыве — через set-дедупликацию: ГИП почти всегда входит и в write-пользователей source (`check_write_permission` даёт ГИПу все разделы проекта, `services.py:2432–2440`), без дедупа ГИП получит два уведомления об одном запросе | Прецедент дедупа: `{task.author, task.project.gip} - {None, user}` (`services.py:5193–5194`) |
| Ф5 | `request_task` валидирует `source_section != target_section` (ValueError) | Диагональный запрос «сам у себя» бессмыслен; диагональные *задания* при этом остаются допустимыми (создание не запрещено — `services.py:5008–5011`; решение по диагонали матрицы — матричный план У3) |
| Ф6 | REQUESTED **не** добавляется в `_NOT_TERMINAL_TASK_STATUSES` (`services.py:5340–5348`), `_NOT_CLOSED_TASK_STATUSES` (`:5500–5507`), `_OPEN_TASK_STATUSES` (`:5490–5495`) — по умолчанию: запрос не «задание в работе», его не считают в overdue Сводки, не включают в Excel-экспорт открытых заданий (`views.py:1993–1999`), по нему не шлют дедлайн-напоминания. Изменить можно решениями У5/У6 (§7) | Состав списков верифицирован; фильтр Excel по `_NOT_TERMINAL_TASK_STATUSES` (`views.py:1993–1999`) автоматически исключит REQUESTED |
| Ф7 | Сегментная видимость REQUESTED — **по разделам**, не по пользователю: «Входящие» += (status=REQUESTED И target_section ∈ write-разделы пользователя), «Исходящие» += (status=REQUESTED И source_section ∈ write-разделы) | К5 формулирует «его раздел — source/target», и у REQUESTED `author=None` до оформления — фильтр «по автору» невозможен в принципе (`services.py:5480/:5482`); альтернатива «только requested_by» — У2 (§7) |

---

## 2. Сверка с DESIGN_TASK_AUDIT.md: готово / не хватает / уточнение

### 2.1. Точки касания REQUESTED из аудита §7.б — верифицированы на свежем клоне

| Точка (аудит §7.б) | Пруф (файл:строка, проверено в этой сессии) | Что требует план |
|---|---|---|
| `DesignTask.Status` — добавить значение | `models.py:707–716` (9 значений); `status` CharField(20) `:768–776` | К1: REQUESTED добавляется в choices; max_length=20 достаточен |
| Матрица переходов | `_DESIGN_TASK_TRANSITIONS` `services.py:5086–5095` (8 переходов); `_DESIGN_TASK_FINAL_STATUSES` `:5101–5104`; `_COMMENT_REQUIRED` `:5107–5111` | К3: REQUESTED в словарь не добавляется (только accept/decline); guard — Ф3 |
| `TASK_STATUS_MAP`/`map_task_status` | `mapping.py:22–33`, `:69–73`; потребитель S3 `sync/export.py:56–65` | К4/Ф2: расширить (§4) |
| `TASK_STATUS_BADGES` | `views.py:275–285` — фоллбэк `text-bg-secondary` (`views.py:1226`) | К6: новый бейдж (§5.4) |
| `_task_card_flags` | `views.py:1690–1732` (`can_cancel` `:1718–1721`; `can_add_input` `:1723–1724`; `can_toggle_checklist` `:1728–1729`) | К6: новые флаги accept/decline/withdraw; can_cancel исключает REQUESTED (Ф3) |
| Сегменты list_tasks | `services.py:5480` (incoming = assignee), `:5482` (outgoing = author); гейт 'all' `:5468–5471` | К5/Ф7: расширение фильтров |
| Сводка: by_status подхватит автоматически | `task_summary_for_gip` `services.py:5351–5371` (values('status').annotate `:5360–5361`); чипы — `views.py:1960–1972` строятся из `DesignTask.Status.choices` — REQUESTED-чип появится сам | К6: счётчик «Запросы: N» — отдельно (§5.6); фиксированные карточки `_summary_cards` `views.py:1922` сами не подхватят |
| «Требуют внимания» не включает новый статус | `tasks_requiring_attention` `services.py:5374–5438` (причины overdue/blocked/review/on_hold) | Осознанно: REQUESTED не попадает (У6) |
| Напоминания | `_OPEN_TASK_STATUSES` `:5490–5495`, `_NOT_CLOSED_TASK_STATUSES` `:5500–5507`, получатели `_reminder_recipients` `:5545–5557` (пустые assignee/author отсеиваются `:5551–5553`) | Ф6: не включаем (У5) |
| Уведомления переиспользуемы | `_notify_task` `services.py:4944–4967` (проверка участника `:4958–4960`; verb'ы не расширялись — комментарий `:4951–4954`; прецеденты сообщений `:5226–5236`) | К3: три уведомления (§3.3–3.5) |
| Кнопка «Выдать задание» и её гейт | `project_detail.html:500–503`; `_can_issue_task` `views.py:1341–1357` (только superuser/admin/gip — уже сейчас уже сервиса `create_task`, который пускает любого write-пользователя `:5010–5011`) | К6: для «Запросить задание» гейт шире (У1) — типовой запрашиватель (КР-разработчик) не ГИП |

### 2.2. Прецеденты, которые план переиспользует

| Прецедент | Пруф | Для чего |
|---|---|---|
| Сервис-валидация «разделы принадлежат проекту» | `services.py:5008–5009` | request_task |
| Срок из `template.default_duration_days` | `services.py:5024–5027` | request_task при пустом due_date |
| Копия чек-листа шаблона (К8) | `services.py:5037–5041` | request_task |
| Создание без лога (первая запись лога — при первом переходе) | `create_task` `services.py:4989–5048` не пишет лог; первый лог — `issue_task` `:5067` | REQUESTED создаётся без лога; accept/decline пишут REQUESTED→ISSUED / REQUESTED→CANCELLED |
| Лог + уведомление в атомарной транзакции | `issue_task` `services.py:5061–5075` | accept/decline |
| Отдельная POST-вьюха действия | `TaskIssueActionView` (urls.py:55), `TaskStatusActionView` (urls.py:109) | task_request_accept / task_request_decline |
| POST-форма кнопкой из таблицы | `project_detail.html:624–631` (кнопка «Выдать») | кнопки карточки |
| Страница-форма по образцу | `task_form.html` (assignee `:99–113`, template `:115–130`, кнопки `:132–141`); `TaskCreateView` `views.py:1359+` (`_resolve_input` `:1453–1527`, исполнитель — участник проекта `:1503–1515`) | форма запроса и форма оформления |
| Разделы формы без COMMON | `views.py:1390–1395` | выбор source/target в форме запроса |
| Ссылка на карточку из вкладки | `project_detail.html:590–592` | REQUESTED-строка в таблице вкладки |

### 2.3. Чего не хватает (факты)

1. Статуса REQUESTED, поля `requested_by` и nullable `author` — не существует (аудит Часть 3: grep `REQUESTED` — только ложные совпадения).
2. Сервисов request/accept/decline — не существует (аудит Часть 1: полный список функций модуля, строк 61–83 отчёта).
3. Guard'а REQUESTED в `update_task_status` — не существует (статуса нет).
4. Секционных фильтров сегментов — не существует (сегменты только по автору/исполнителю, `services.py:5480/:5482`).
5. UI запроса — не существует.
6. Помощника «write-пользователи раздела» для уведомлений — не существует: `check_write_permission` (`services.py:2414–2449`) отвечает точечно «может ли этот пользователь», обратной выборки «кто может» нет (единственный материал: `UserProfile.allowed_sections` M2M `models.py:409`, `ProjectMembership.role_in_project` `models.py:431`).
7. Ключей REQUESTED в обоих маппингах — не существует (`mapping.py:22–33`, `external_api/serializers.py:11–21`).

### 2.4. Уточнение аудита (новый факт этой сессии)

Аудит §7.б зафиксировал как жёсткую точку «TASK_STATUS_MAP + их TaskStatus» и упоминал `stream_cad_status` в API-нагрузке (`mapping.py:159`), но не знал, что внешний API сериализует задания **собственным** сериализатором: `ApiTaskListView` (`external_api/views.py:449–477`) использует `ApiTaskReadSerializer` (`external_api/serializers.py:30–52`), внутри которого свой `GA_STATUS_MAP` (`:11–21`) с fallback-`.get(status, 'NOT_STARTED')` (`:54–55`). Следствия: (а) внешний API на REQUESTED не упадёт; (б) состав этого маппинга отличается от S3-карты (ON_HOLD→REWORK против NOT_STARTED; COMPLETED→REVIEW против DONE) — унифицировать карты в этом плане нельзя, это два зафиксированных контракта (раздел 10.4 плана RFI для API; S3-схема 1.0 для tasks.json). Состав GA `TaskStatus` — 6 значений (`gip-assistant` `backend/src/domain/value_objects/enums.py:17–23`: NOT_STARTED, IN_PROGRESS, REVIEW, REWORK, DONE, CANCELLED), REQUESTED там нет — расширять их сторону не планируем, отличимость обеспечивает `stream_cad_status` (`mapping.py:159`).

---

## 3. Дизайн сервисов (сигнатуры)

Место — `storage/domain/services.py`, рядом с задачным блоком (`create_task` `:4989+`). Вьюхи тонкие (прецедент docstring `TaskDetailView`/`_task_card_flags` `views.py:1693–1700`).

### 3.1. Модель и миграция (К2, Ф1)

Изменения `storage/domain/models.py`:

1. `DesignTask.Status` — добавить `REQUESTED = 'REQUESTED', 'Запрошено'` (10 значений; `models.py:707–716`).
2. `DesignTask.requested_by` — FK User, `null=True, blank=True, on_delete=PROTECT, related_name='requested_design_tasks'` (К2). PROTECT — «кто запросил» нельзя потерять молча; симметрия с `author`/`closed_by` PROTECT (`models.py:744–750`, `:781–788`).
3. `DesignTask.author` — `null=True, blank=True` (Ф1); on_delete PROTECT и related_name не меняются (`models.py:744–750`).

Одна миграция `0032_design_task_requested`: AddField requested_by + AlterField author + AlterField status (choices — схему CharField(20) не меняет, но makemigrations зафиксирует метаданные). Проверка К2 «не сломает ли null constraint'ы и формы»:

- **Constraint'ы — нет**: единственное ограничение миграции 0031 — `uniq_tasktemplate_company_title` на TaskTemplate (`0031:126–129`), индекс `design_task_proj_src_tgt_idx` (`:130–133`) поля не затрагивают; собственных CheckConstraint у DesignTask в 0031 нет (CreateModel `:25–102`).
- **Данные — нет**: все существующие задания имеют автора (создаются только через `create_task`, который всегда ставит `author=user`, `services.py:5029–5041`; админка — форма требует автора). Backfill не нужен.
- **Формы — одна точка**: `TaskCreateView` всегда подставляет автора при создании (POST-ветка `views.py:1528–1560`) — пустой author появится только через request_task; в шаблонах колонка «Автор» (`project_detail.html:604`, `task_dashboard.html` ~:102) рендерит `task.author.get_full_name|default:task.author.username` — для None Django-шаблон молча вернёт пусто, но строки REQUESTED должны показывать «Запросил: {{ task.requested_by }}» (§5.3); админка `list_display` содержит author (`admin.py:86–95`) — None отобразится как «—» штатно.

Правило репо: обновить `ARCHITECTURE.md` (модель изменилась — правило 4 Инструкции).

### 3.2. Помощники прав (новые, для фильтров и уведомлений)

1. `user_writable_section_ids(user, project) -> set[int]` — та же семантика, что `check_write_permission` (`services.py:2414–2449`), но списком ID для QuerySet-фильтра: superuser → все разделы проекта; admin → все разделы проекта (гейт компании `:2429–2430`); gip (project.gip или membership gip, `:2432–2440`) → все разделы проекта; developer/contractor → `profile.allowed_sections` ∩ разделы проекта (`:2452`, M2M `models.py:409`); прочие роли → пусто. COMMON не исключается на уровне помощника (исключение COMMON — обязанность формы, прецедент `views.py:1390–1395`).
2. `get_section_write_recipients(section) -> set[User]` — получатели уведомлений по разделу: write-пользователи (как выше, по этому разделу) + ГИП проекта; дедуп set'ом (Ф4); каждый кандидат проверяется `_is_project_participant` внутри `_notify_task` (`:4958–4960`) — за пределы проекта уведомления не уйдут.

Права на действия по-прежнему проверяют сервисы по одному пользователю (`check_write_permission`) — помощники только для массовых выборок и рассылок; вьюха не дублирует (прецедент комментария `views.py:1218–1220`).

### 3.3. `request_task` (К1, К3, К8, Ф5)

Сигнатура (преемственность с `create_task` `services.py:4989–5048`):

```
request_task(user, project, source_section, target_section, title,
             description, due_date=None, template=None,
             priority='normal') -> DesignTask
```

Поведение:
1. Валидации: разделы принадлежат проекту (прецедент `:5008–5009`); `source_section != target_section` (Ф5); `check_write_permission(user, target_section)` — запрашивает представитель **цели** (`:2414–2449`); `priority in DesignTask.Priority.values` (прецедент `:5013–5014`).
2. Создание в `transaction.atomic()`: `status=REQUESTED, author=None, requested_by=user` (К1); срок из `template.default_duration_days` при пустом due_date (прецедент `:5024–5027`); копия чек-листа шаблона bulk_create (К8, прецедент `:5037–5041`).
3. Лог — не пишется при создании (прецедент create_task; первая запись лога появится на accept/decline). Момент создания виден через `created_at`.
4. Уведомления (К3): `get_section_write_recipients(source_section)` — «Раздел {target.code} запрашивает задание: «{title}»» + ГИПу; verb `'status_changed'` (verb'ы не расширялись — прецедент комментария `:4951–4954`); дедуп Ф4.
5. Логирование `logger.info` по образцу `:5042–5047`.

Чек-лист у REQUESTED: переключение `toggle_checklist_item` требует assignee или gip/admin (`services.py:5316–5317`) — у REQUESTED исполнителя нет, чек-лист до оформления правит только ГИП/админ (факт, фиксируем в плане; не меняем).

### 3.4. `accept_task_request` (К3)

```
accept_task_request(user, task, title=None, description=None,
                    due_date=None, assignee=None) -> DesignTask
```

1. Гейт статуса: `task.status == REQUESTED`, иначе ValueError (прецедент issue_task `:5057–5058`).
2. Права: `check_write_permission(user, task.source_section)` **или** `_is_gip_or_admin(user, task.project)` (К3; `_is_gip_or_admin` `services.py:4916–4924`).
3. Правки полей: переданные title/description/due_date перезаписывают запрос (пустые — остаются как просили); assignee — опционален, при передаче проверяется `_is_project_participant(assignee, task.project)` (прецедент формы `views.py:1503–1515`); `author=user` (оформляющий источник, К1), `issued_at=timezone.now()` (прецедент `:5063–5066`), `status=ISSUED`. `requested_by` сохраняется (для карточки и истории).
4. Лог REQUESTED→ISSUED (`_log_status_change` `:4970–4980`); уведомление `requested_by`: «Ваш запрос «{title}» оформлен как задание #{task.pk}» (verb `'assigned'` — прецедент выдачи `:5068–5075`).
5. Задание ISSUED без исполнителя валидно (issue_task уведомляет assignee только если он есть, `:5069–5075`) — рекомендация У3.

### 3.5. `decline_task_request` (К3)

```
decline_task_request(user, task, comment: str) -> DesignTask
```

1. Гейт статуса REQUESTED (как выше).
2. Права (К3, три пути): `check_write_permission(user, task.source_section)` / `_is_gip_or_admin` / `user == task.requested_by` (отзыв собственного запроса).
3. `comment` обязателен: пустой/пробельный → ValidationError (К9: тест «decline с пустым комментарием → ValidationError»; прецедент обязательного комментария `_COMMENT_REQUIRED` `:5107–5111`, проверка `:5151–5156`).
4. Статус CANCELLED (REQUESTED не финальный — отмена разрешена семантикой `_DESIGN_TASK_FINAL_STATUSES` `:5101–5104`); `closed_by` не ставится — прецедент: update_task_status в CANCELLED-ветке его не ставит (`:5126–5131`, save `:5174–5182`).
5. Лог REQUESTED→CANCELLED с комментарием (причина сохраняется в `DesignTaskStatusLog.comment` — прецедент RETURNED `:5221–5222`).
6. Уведомления: отклонение источником/ГИПом → `requested_by` («Ваш запрос отклонён. Причина: …»); отзыв запрашивателем → `get_section_write_recipients(source_section)` («Запрос «{title}» отозван»); дедуп Ф4.

### 3.6. Guard в `update_task_status` и флаги карточки (К3, Ф3)

1. `update_task_status` (`services.py:5114–5251`): после проверки «новый статус совпадает» (`:5122–5123`) и **до** CANCELLED-ветки (`:5126`) — early guard: `old_status == REQUESTED → PermissionDenied("Запрос обрабатывается только через оформление/отклонение")`. Это закрывает обход через «Любой → CANCELLED» (`:5126–5131`) и гарантирует, что ни один переход REQUESTED не пройдёт через общий конвейер (в словаре `:5086–5095` REQUESTED-пар нет — они бы упали в ValidationError `:5133–5148`, но CANCELLED-ветка словарь не читает).
2. `_task_card_flags` (`views.py:1690–1732`): `can_cancel` исключает REQUESTED (`:1718–1721` — условие `status != REQUESTED`); новые флаги: `can_accept` = `check_write_permission`-подсказка по source или `can_manage` (точную проверку делает сервис — «подсказка UI», прецедент docstring `:1693–1700`); `can_decline` = те же, кроме requested_by; `can_withdraw` = `is_requester` (`task.requested_by_id == user.pk`); `can_add_input` — см. У7.

### 3.7. Сегменты видимости (К5, Ф7)

Ответ на проверку К5 «попадает ли REQUESTED туда автоматически»: **нет**. `list_tasks` фильтрует по пользователю: `incoming → assignee=user` (`services.py:5480`), `outgoing → author=user` (`:5482`) — у REQUESTED оба поля пусты, задание не видно никому, кроме сегмента «Все» (ГИП/админ, `:5468–5471`). Сегмент-контрол вкладки строится из `can_view_all_tasks` (`views.py:1147–1152`), декор строк и фильтры работают поверх результата `list_tasks` (`views.py:1155–1213`) — менять нужно именно сервис.

Расширение `list_tasks` (аддитивное, Ф7):

```
incoming: (assignee=user) | (status=REQUESTED & target_section_id ∈ user_writable_section_ids(user, project))
outgoing: (author=user) | (status=REQUESTED & source_section_id ∈ user_writable_section_ids(user, project))
```

- `user_writable_section_ids` вычисляется один раз на вызов (1–2 запроса: memberships/allowed_sections) и оборачивается в `Q`-объекты; старые ветки для не-REQUESTED заданий не меняются (регресс зелёный).
- «Все» (ГИП/админ) видит REQUESTED автоматически — фильтр 'all' не трогается.
- Подделанный `?role_filter=all` не-ГИПом откатывается как прежде (`views.py:1155–1162`).
- Риск утечки отсутствует: `user_writable_section_ids` ограничен разделами проекта пользователя; выборка всё равно идёт внутри `DesignTask.objects.filter(project=project)` (`:5473–5477`) и `_ensure_project_access` (`:5465`).

---

## 4. Маппинги и внешний контракт (К4, Ф2)

### 4.1. S3-блокер — подтверждён

`build_tasks_payload` (`sync/export.py:56–65`) сериализует **все** задания проекта (`project.design_tasks`, без фильтра статусов) через `serialize_design_task` (`mapping.py:130–164`), который вызывает `map_task_status` (`:134` → `:69–73`). Первый же REQUESTED в БД уронит tasks.json с `ValueError: map_task_status: неизвестный статус DesignTask: 'REQUESTED'`. Поэтому статус и карта едут **одним этапом R1** (атомарно: между «миграция применена» и «карта расширена» прод уязвим).

### 4.2. Внешний API — не падает (уточнение Ф2), рекомендация

`ApiTaskListView` (`external_api/views.py:449–477`) отдаёт `ApiTaskReadSerializer` (`external_api/serializers.py:30–52`): поле `status` — сырой строка-статус (`:37`), `status_label` — `get_status_display` (`:38`) подхватит REQUESTED автоматически из choices; `ga_status` — `GA_STATUS_MAP.get(task.status, 'NOT_STARTED')` (`:54–55`) — fallback, не исключение. `author=None` безопасен: `_user_data(None) → None` (`:24–27`). Рекомендация: добавить в `GA_STATUS_MAP` явный ключ `REQUESTED: 'NOT_STARTED'` — поведение то же, что fallback, но карта остаётся самодокументированной и полный (тест «все статусы в карте» по прецеденту `MappingStatusTests.test_all_statuses_mapped`, `test_s3_sync.py:102–121`).

### 4.3. Значение REQUESTED в `TASK_STATUS_MAP`: NOT_STARTED (рекомендация)

| Вариант | Эффект у ГИП-Ассистента | Оценка |
|---|---|---|
| **NOT_STARTED (рекомендация)** | «Работы ещё нет — есть только запрос». REQUESTED попадает в `GA_OPEN_STATUSES` (`mapping.py:34–35`) → дедлайн-метрики живые (`compute_deadline_metrics` `mapping.py:116–124`): их матрица покажет просроченный запрос парой OVERDUE (`matrix_service.py:129–135` — статус не в {DONE, CANCELLED} и срок прошёл), зависший запрос — NOT_STARTED | Честная семантика; запрос остаётся «открытым» в их конвейере; точный статус доступен через `stream_cad_status` (`mapping.py:159`) |
| REVIEW | «Ждёт действия источника» | Их матрица REVIEW в статус ячейки не превращает (провал в NOT_STARTED — аудит §5.2.2, дефект 2) — визуально ничем не лучше NOT_STARTED; в их UI REVIEW = «на проверке», что вводит в заблуждение (никто не проверяет) |
| REWORK | «На доработке» | Ложь: работу не возвращали — её не начинали |

Итог: `TASK_STATUS_MAP += {'REQUESTED': 'NOT_STARTED'}` + тест-утверждение полного покрытия (К9: «экспорт tasks.json не падает на REQUESTED»).

---

## 5. UI-флоу пользователя по шагам (К6)

### 5.1. Шаг 1 — вход: кнопка «Запросить задание»

- Тулбар вкладки «Задания» (`project_detail.html:493–512`): новая кнопка рядом с «Выдать задание» (`:500–503`) — «Запросить задание» → `/projects/<id>/tasks/new/?mode=request`.
- Видимость: `{% if can_request_task %}` — новый контекст-флаг. Кандидаты гейта: (а) все участники проекта; (б) участники с ≥1 write-разделом (`user_writable_section_ids` непуст); (в) как `_can_issue_task` (ГИП/админ, `views.py:1341–1357`). Рекомендация — (б): типовой запрашиватель — разработчик КР, не ГИП; гейт «Выдать задание» уже шире сервиса, повторять эту асимметрию не нужно (У1).

### 5.2. Шаг 2 — форма запроса

- Рекомендация: отдельная вьюха `TaskRequestCreateView` + шаблон `task_request_form.html` по образцу `task_form.html`; альтернатива `?mode=request` внутри `TaskCreateView` (`views.py:1359–1564` уже сложный) — У4.
- Поля (К6): source_section — выбор из всех разделов проекта без COMMON (`views.py:1390–1395` прецедент); target_section — «ваш раздел»: выбор из `user_writable_section_ids` (подставлен автоматически, если он один); title; description; due_date (опц.); template (опц., тот же справочник активных шаблонов компании `views.py:1422–1425`, автозаполнение по data-атрибутам — прецедент `task_form.html:117–131`); priority (по умолчанию normal).
- Сабмит → `request_task` → redirect на вкладку «Задания» с messages (прецедент редиректа `TaskCreateView` `:1553–1560`); при ошибке — форма с сообщением.
- Подсказка под формой: «Запрос увидят представители раздела {source} и ГИП».

### 5.3. Шаг 3 — видимость запроса

- Запрашиватель и write-состав target видят запрос во «Входящие» (Ф7); write-состав source — в «Исходящие»; ГИП — в «Все». В таблице вкладки REQUESTED-строка: колонка «Автор» показывает «Запросил: {requested_by}» вместо пустоты (условие `{% if task.status == 'REQUESTED' %}` в ячейке автора, `project_detail.html:604`); бейдж REQUESTED (§5.4); кнопка «Оформить»/«Отклонить» не в таблице — только в карточке (таблица остаётся списком).
- Карточка открывается по заголовку (`project_detail.html:590–592`) — маршруту `task_detail` (`urls.py:104`) правки не нужны.

### 5.4. Шаг 4 — карточка REQUESTED

- Баннер после шапки (вставка после `task_detail.html:54`, до блока «Суть» `:68–72`): карточка-блок на токене `--s-review-subtle` (или токен бейджа — У5): «Запрос задания: раздел {target.code} просит раздел {source.code} выполнить работу. Запросил: {requested_by}» + причина отклонения из лога после decline.
- Кнопки (К6): «Оформить задание» (primary) — для write-пользователей source и ГИПа/админа (`can_accept`); «Отклонить» (outline-danger) — те же права (`can_decline`), раскрывает textarea комментария (обязателен) и POST; «Отозвать запрос» (outline-secondary) — только `requested_by` (`can_withdraw`). POST-вьюхи по образцу `TaskStatusActionView` (`urls.py:109`): `task_request_accept` / `task_request_decline`.
- «Оформить задание» ведёт на страницу-форму оформления (`/tasks/<id>/accept/`, шаблон по образцу `task_form.html`): поля title/description/due_date предзаполнены значениями запроса, assignee — выбор из участников проекта (прецедент `views.py:1503–1515`) — inline-форма на карточке как альтернатива (У3). Сабмит → `accept_task_request` → redirect на карточку (теперь ISSUED, обычный конвейер).
- Строка «Чек-лист» уже существует (К8: скопирован при создании); правка пунктов до оформления — только ГИП/админ (факт §3.3.6).
- Обычные статус-кнопки (принято в работу/выполнено/…) на REQUESTED не показываются: `_task_card_flags` вернёт их False по статусу (`views.py:1708–1721` — статусы ISSUED/IN_PROGRESS/COMPLETED не совпадают); «Отменить» скрыта (Ф3).

### 5.5. Бейдж REQUESTED (К6)

- `TASK_STATUS_BADGES` (`views.py:275–285`) — добавить REQUESTED. Все 9 существующих классов заняты разными смыслами (инфо=IN_PROGRESS, warning=ON_HOLD, secondary=DRAFT, primary=ISSUED…); повторное использование размывает «один смысл — один цвет». Рекомендация: новый класс `.badge-task-requested` в `redesign.css` на токенах `--s-wait-*` (subtle-фон, `--s-wait-on` текст, маркер solid) + иконка `bi-hourglass` в карточке; tokens.css не трогается (автогенерат, `static/design-tokens/tokens.css:1–2`); правило «статус не кодируется только цветом» соблюдается (иконка + слово «Запрошено», правило №3 `tokens.css:22–28`). Альтернатива — `text-bg-warning` (конфликт с ON_HOLD) — У5.

### 5.6. Сводка «Запросы: N» (К6)

- Согласование с матричным планом: матрица живёт вкладкой `?tab=matrix` на project_detail (матричный план §4.1) и **не трогает** `task_dashboard.html`; правка Сводки у REQUESTED-плана одна, конфликтов двух фич нет.
- Размещение: блок «Запросы заданий» по образцу RFI-блока (`task_dashboard.html:140–159` — прецедент второго агрегата-блока): карточка-счётчик «Запросы: N» (REQUESTED count из `task_summary_for_gip.by_status` — сервис не меняется, `services.py:5351–5371`) + мини-таблица REQUESTED (запрос, запросил, пара, срок) с ссылками на карточки.
- Чип статуса REQUESTED на Сводке появится автоматически (`_status_chips` из choices, `views.py:1960–1972`); клик по нему ведёт на `?tab=tasks&status=REQUESTED` — фильтр вкладки принимает любое значение choices (`views.py:1174–1176`), правки не нужны.
- `_summary_cards` (5 фиксированных, `views.py:1922`) не расширяется (Ф6/У6: «Запросы» — отдельный блок, не шестая карточка метрик заданий).

---

## 6. Связка с матрицей (К7)

1. Заготовка матричного плана соблюдается: матрица на REQUESTED не ломается (whitelist-семантика, Р6 `MATRIX_TASKS_PLAN.md:296`); REQUESTED-задачи пары попадают только в «всего», статус ячейки остаётся по цепочке К1 матричного плана (пара только с запросами → NOT_STARTED, пара с ON_HOLD → WAITING_INFO).
2. Кнопка «Запросить задание» в drill-down WAITING_INFO-ячейки: drill-down матричного плана (`MATRIX_TASKS_PLAN.md:207`, §4.6) ведёт на `?tab=tasks&task_source={sid}&task_target={tid}`; тулбар вкладки в этом режиме показывает «Запросить задание» с предзаполненной парой → `/tasks/new/?mode=request&source={sid}&target={tid}` (форма читает GET-параметры и фиксирует разделы). WAITING_INFO = «есть ON_HOLD» — сегмент «жду ИД»: логично запросить недостающее задание напрямую.
3. Порядок внедрения: drill-down появляется в этапе M4 матричного плана; кнопка запроса в drill-down — этап R5 этого плана, **выполняется после обеих фич** (матрица не реализована на HEAD — проверено: ни вкладки, ни сервиса нет). Пока матрица не внедрена, связка остаётся в плане как зависимость, не блокер.
4. Решение о будущем 8-м статусе ячейки «Запрошено» остаётся за Координатором в рамках матричного плана (У8 `MATRIX_TASKS_PLAN.md:228`); этот план его не предрешает.

---

## 7. UX-вопросы на решение Координатора

| № | Вопрос | Варианты | Рекомендация |
|---|---|---|---|
| У1 | Кому видна кнопка «Запросить задание» | (а) всем участникам проекта; (б) участникам с ≥1 write-разделом; (в) как «Выдать задание» — только ГИП/админ (`views.py:1341–1357`) | (б): типовой запрашиватель — разработчик КР, не ГИП; у ролей без write-разделов кнопка бесполезна |
| У2 | Кому виден REQUESTED во «Входящих» target-раздела | (а) всему write-составу target (Ф7, симметрично source); (б) только requested_by | (а): запрос идёт от имени раздела; до оформления «исполнителя запроса» со стороны источника не существует — симметрия честнее |
| У3 | Форма «Оформить задание» | (а) отдельная страница-форма `/tasks/<id>/accept/` (прецедент task_form); (б) inline-форма на карточке | (а): три редактируемых поля + выбор исполнителя — inline тесно; прецедент страница-форма есть |
| У4 | Форма запроса | (а) отдельная вьюха+шаблон `TaskRequestCreateView`/`task_request_form.html`; (б) `?mode=request` внутри `TaskCreateView` (`views.py:1359–1564`) | (а): состав полей и логика сабмита различаются; TaskCreateView уже несёт draft/issue-ветки |
| У5 | Бейдж REQUESTED | (а) новый класс на токенах `--s-wait-*` в redesign.css; (б) `text-bg-warning` (конфликт с ON_HOLD `views.py:279`) | (а): свободного Bootstrap-класса нет; токены дают AA-контраст и систему |
| У6 | «Требуют внимания» и напоминания о зависших запросах | (а) REQUESTED не включается ни туда ни туда (Ф6); (б) включить в `_NOT_CLOSED_TASK_STATUSES` → просроченные запросы напомнят ГИПу (единственный живой получатель: author/assignee пусты — `_reminder_recipients` `:5551–5553`) | (а) для MVP: у запроса мягкий срок-пожелание; зависшие запросы видны в блоке «Запросы: N» Сводки (§5.6) |
| У7 | Кто прикладывает input-вложения к REQUESTED | (а) как сейчас: `can_add_input` = author/gip (`views.py:1723–1724`) → у REQUESTED только ГИП; (б) добавить requested_by | (а) для MVP: уточнения — в тексте запроса; input-документы появятся при оформлении |
| У8 | Приоритет REQUESTED в цепочке матрицы (будущее) | (а) остаётся У8-а матричного плана — REQUESTED в ячейке не влияет на статус; (б) новый статус ячейки «Запрошено» | (а): WAITING_INFO уже подсвечивает пары «жду ИД»; решение принять при внедрении матрицы |

---

## 8. Тесты (К9)

Точечный файл `storage/tests/test_design_tasks_request.py` (рядом с `test_design_tasks.py`, 28 тестов; фабрики/фикстуры — по образцу существующих задачных тестов).

### 8.1. Сервисы (TestCase)

- `request_task`: создаёт REQUESTED с author=None, requested_by=user, чек-лист скопирован из шаблона (К8), срок из `default_duration_days` при пустом due_date; write на target обязателен — negative без прав → PermissionDenied; чужой проект → ValueError; source == target → ValueError (Ф5); уведомление write-пользователям source + ГИПу, дедуп (ГИП один раз) (Ф4).
- `accept_task_request`: только write на source или gip/admin (negative: запрашиватель и посторонний → PermissionDenied); правит title/description/due_date, author=user, status=ISSUED, issued_at; assignee-участник (negative: не-участник); лог REQUESTED→ISSUED; уведомление requested_by; на не-REQUESTED → ValueError.
- `decline_task_request`: пустой комментарий → ValidationError (К9); права три пути (source-write/gip/requested_by); посторонний → PermissionDenied; статус CANCELLED; лог с комментарием; уведомление requested_by; отзыв requested_by уведомляет source-состав; на не-REQUESTED → ValueError.
- `update_task_status` guard: любой переход из REQUESTED (включая CANCELLED через «Любой → CANCELLED» ГИПом) → PermissionDenied (Ф3).

### 8.2. Сегменты и видимость (TestCase)

- REQUESTED виден запрашивателю во «Входящие» и write-составу source в «Исходящие» (Ф7); не виден постороннему разработчику того же проекта без прав на разделы пары; ГИП видит в «Все»; подделка `?role_filter=all` не-ГИПом откатывается (регресс `test_tasks_tab_all_requested_by_developer_falls_back`, `test_design_tasks_views.py:176`).

### 8.3. Экспорт и маппинги (TestCase, по образцу `MappingStatusTests` `test_s3_sync.py:102+`)

- Полное покрытие `TASK_STATUS_MAP` после расширения (10 статусов);
- `build_tasks_payload` на проекте с REQUESTED не падает, в payload попадает `status='NOT_STARTED'`, `stream_cad_status='REQUESTED'` (К9: «экспорт tasks.json не падает»);
- `GA_STATUS_MAP` полный; внешний API на REQUESTED отдаёт `status='REQUESTED'`, `ga_status='NOT_STARTED'`, `author=None` без ошибки (Ф2).

### 8.4. Вьюхи и UI-флаги

- Карточка REQUESTED: баннер и три кнопки по правам (can_accept/can_decline/can_withdraw); «Отменить» скрыта для всех (Ф3); POST accept/decline вьюхи — редиректы и messages; форма запроса: target ограничен write-разделами, COMMON отсутствует в обоих селектах; кнопка «Запросить задание» по гейту У1.

### 8.5. Регресс

170 тестов модуля («Задания»: 28+87+24+31) остаются зелёными — изменения аддитивны; S3-синк тесты (`test_s3_sync.py`) зелёные после расширения карты.

---

## 9. Этапы реализации (для Кодера) со СТОП-точками

| Этап | Состав | СТОП-точка |
|---|---|---|
| R1 | Модель: REQUESTED в choices, поле requested_by, author null+blank; одна миграция 0032; оба маппинга (`TASK_STATUS_MAP += REQUESTED→NOT_STARTED`; явный ключ в `GA_STATUS_MAP`); бейдж-класс (У5); `ARCHITECTURE.md` | **СТОП-1**: диф на проверку Аудитору до ОК. Критерий: миграция одна; S3-экспорт не падает (тест §8.3); `makemigrations --check` чистый; ARCHITECTURE.md обновлён. Атомарность: этап неделим — между миграцией и картами экспорт уязвим (§4.1) |
| R2 | Сервисы: `user_writable_section_ids`, `get_section_write_recipients`, `request_task`, `accept_task_request`, `decline_task_request`; guard `update_task_status`; сегменты `list_tasks` (Ф7); тесты §8.1–8.2 | **СТОП-2**: диф. Критерий: все negative-кейсы зелёные; дедуп уведомлений; guard закрывает CANCELLED-обход |
| R3 | UI: кнопка+гейт (У1), форма запроса (У4), POST-вьюхи accept/decline + маршруты, карточка (баннер, три кнопки, страница-форма оформления У3), флаги `_task_card_flags`, бейдж в `TASK_STATUS_BADGES`, колонка «Автор» для REQUESTED; скриншоты | **СТОП-3**: диф + скриншоты по прецеденту `docs/audit/design-task-stageN/`. Критерий: User Flow по шагам §5.1–5.4 проходит; НЕТ UI — НЕТ ФИЧИ соблюдено |
| R4 | Сводка: блок «Запросы: N» (§5.6); финализация У5/У6 (напоминания — по решению); тесты вьюх §8.4 | **СТОП-4**: диф. Критерий: счётчик совпадает с by_status; чип REQUESTED кликабелен |
| R5 | Связка с матрицей: кнопка в drill-down WAITING_INFO с предзаполненной парой (§6.2) — только после внедрения матрицы (M4 матричного плана) | **СТОП-5**: диф. Критерий: предзаполнение пары корректно; иначе этап отложен без ущерба |
| R6 | Регресс 170 тестов + S3-тесты; документация: запись в `docs/PROGRESS.md`, синхронизация `docs/CONTEXT.md:19–22`/`README.md:34` (устарели — аудит Часть 6) по согласованию | **СТОП-6**: полный прогон + финальный аудит; после ОК — коммит и пуш по правилам репо (Кодер) |

Зависимости: R1 → R2 → R3 → R4 → (R5 после матрицы) → R6. R1–R4 от матричного плана не зависят; матрица от R1 не зависит (заготовка У8-а).

---

## 10. Риски

| № | Риск | Факт-основание | Митигация |
|---|---|---|---|
| Р1 | Миграция на проде: ALTER FK author (null) + новая FK requested_by | 0031 не содержит constraint'ов на author (`0031:126–133` — только TaskTemplate-уникальность и индекс); таблица DesignTask мала (компания 25 человек); данные без backfill (§3.1) | Одна миграция, аддитивная; деплой одним патчем по правилам репо; откат патча безопасен (новая FK nullable) |
| Р2 | Окно уязвимости S3-экспорта между миграцией и картой | `build_tasks_payload` берёт все задания (`sync/export.py:56–65`), `map_task_status` падает на неизвестном (`mapping.py:69–73`) | Этап R1 атомарен (миграция + обе карты + тест «не падает» одним дифом); celery-beat-задача S3-экспорта на площадке — запуск после ОК СТОП-1 |
| Р3 | Двойные уведомления (ГИП = write-пользователь source + получатель «ГИПу») | `check_write_permission` даёт ГИПу все разделы проекта (`services.py:2432–2440`) | Дедуп set'ом (Ф4) + тест §8.1 |
| Р4 | Обход decline через update_task_status (CANCELLED-ветка минует матрицу) | `:5126–5131` не читает `_DESIGN_TASK_TRANSITIONS`; REQUESTED не в финальных `:5101–5104`; кнопка `can_cancel` показывается ГИПу (`views.py:1718–1721`) | Guard до ветки (Ф3) + исключение из can_cancel + тест §8.1 (последний кейс) |
| Р5 | Конфликт со Сводкой: REQUESTED-чип появится автоматически, метрики — нет | Чипы из choices (`views.py:1960–1972`); карточки фиксированы (`views.py:1922`); overdue не считает REQUESTED (`services.py:5340–5348` — REQUESTED не в списке) | Отдельный блок «Запросы: N» (§5.6); состав `_NOT_TERMINAL_TASK_STATUSES` не трогается (Ф6); подпись блока про семантику запроса |
| Р6 | Утечка видимости: секционные сегменты показывают запрос всему write-составу раздела | Ф7; `user_writable_section_ids` ограничен проектом и профилем | Выборка внутри `filter(project=…)` + `_ensure_project_access` (`:5465`); тест §8.2 (negative кейс) |
| Р7 | Рассинхрон двух маппингов при будущих изменениях | `TASK_STATUS_MAP` (`mapping.py:22–33`) и `GA_STATUS_MAP` (`external_api/serializers.py:11–21`) уже различаются составом | Тесты полного покрытия обеих карт (§8.3); унификацию сознательно не делаем — разные контракты (§2.4) |
| Р8 | REQUESTED и матрица: пары-запросы показываются как NOT_STARTED | Whitelist-семантика матричного плана (Р6 `MATRIX_TASKS_PLAN.md:296`, У8-а `:228`) | Осознанно; пересмотр — У8 (§7) при внедрении матрицы |
| Р9 | Напоминания по REQUESTED при включении в `_NOT_CLOSED_TASK_STATUSES` уходят только ГИПу | author/assignee пусты, отсеиваются (`:5551–5553`) | По умолчанию не включаем (Ф6/У6); если включат — помнить про получателя-ГИПа |

---

## 11. Чек-лист приёмки (перед ОК на СТОП-6)

- [ ] Одна миграция 0032; `makemigrations --check` чистый; ноль новых сущностей (К1/К2).
- [ ] `ARCHITECTURE.md` обновлён (правило 4 Инструкции).
- [ ] Обе карты расширены и покрыты тестами «полный состав» (§8.3); S3-экспорт на проекте с REQUESTED не падает (К9).
- [ ] Guard REQUESTED в `update_task_status` до CANCELLED-ветки; кнопка «Отменить» на REQUESTED недоступна (Ф3).
- [ ] REQUESTED виден: target-составу во «Входящие», source-составу в «Исходящие», ГИПу в «Все»; посторонним не виден (К5/Ф7).
- [ ] User Flow §5.1–5.4 проходит: кнопка → форма (COMMON отсутствует, target по write) → карточка (баннер, «Оформить»/«Отклонить»/«Отозвать») → ISSUED/CANCELLED.
- [ ] Чек-лист шаблона скопирован при создании запроса (К8).
- [ ] Уведомления: создание → source-состав+ГИП (без дублей), accept → requested_by, decline → requested_by/отзыв → source-состав (К3/Ф4).
- [ ] Бейдж REQUESTED: один смысл — один цвет; иконка+слово (не только цвет); tokens.css не изменён (§5.5).
- [ ] Сводка: блок «Запросы: N»; чип REQUESTED кликабелен; Excel открытых заданий запросы не содержит (Ф6).
- [ ] Регресс: 170 задачных тестов + S3-тесты зелёные (§8.5).
- [ ] Терминология: RFI не упоминается в UI запросов; «Сводка» — единственный термин страницы метрик.

---

*План не содержит кода реализации — сигнатуры, составы, пруфы и точки решения. Все пруфы проверены на свежем клоне `ed8c2b1` в этой сессии; сверка с аудитом и матричным планом выполнена по их локальным копиям (базы совпадают с HEAD).*
