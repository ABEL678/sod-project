# Stage 1 — P0-фиксы ИИ-контура Stream-CAD (UX-аудит)

Базовый коммит: main @ cdb88c1
Ветка с применёнными патчами: audit/stage1-p0 (коммит 7b3b540)

## Состав

| Файл | Что содержит |
|---|---|
| stage1-p0-combined.patch | Все изменения одним патчем (6 файлов) |
| 01-project_detail.patch  | P0-1, P0-2: вкладки → URL-состояние, взаимоисключающие панели, .ai-tabpane |
| 02-_report_result.patch  | P0-3: period_label вместо report.period |
| 03-ai_checks_views.patch | P0-3: period_label в контексте report_generate |
| 04-storage_views.patch   | P0-1/P0-2: whitelist вкладок + get_template_names читает ?tab= |
| 05-_panel.patch          | P0-4: крестик только у открытой панели, Esc, без hiddenByUser |
| 06-app_css.patch         | P0-1: .app-tabs-wrap, .ai-tabpane, .ai-tabpane__scroll |
| final-files/             | Итоговые версии всех 6 файлов целиком |

## Применение

Из корня репозитория (на чистом main @ cdb88c1):

    git apply --check stage1-p0-combined.patch   # проверка
    git apply stage1-p0-combined.patch           # применение

Или по отдельности (порядок не важен, файлы не пересекаются):

    git apply 01-project_detail.patch
    git apply 02-_report_result.patch
    ...

## Проверка после применения

    python -m py_compile ai_checks/views.py storage/adapters/views.py

Ручной смоук:
1. Страница проекта → вкладка «ИИ-поиск»: URL становится ?tab=ai-search,
   таблица документов замещается панелью поиска, кнопка «Назад» возвращает.
2. Вкладка «Отчёт ГИПу» → «Сгенерировать»: строка «Отчёт за период
   08.10.2026 — 07.11.2026 · сгенерирован …».
3. Вьювер документа: FAB «Проверить» → крестик появляется только у открытой
   панели; после закрытия FAB остаётся; Esc закрывает панель.
4. Мобильная ширина: контент вкладок прокручивается внутри панели,
   таблица документов не перекрывается.
