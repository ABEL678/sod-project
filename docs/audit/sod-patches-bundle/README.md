# Stream-CAD — сводный пакет патчей (unified diff)

Дата сборки: 2026-11-08 (ревизия 3 — перегенерация от `b7c702b` + хотфикс
`open_pdf`). Репозиторий: github.com/ABEL678/sod-project (main).

Состав — патчи по трём заданиям:

```
sod-patches-bundle/
├── README.md                                  ← этот файл
├── stage20-final-ai-chain/                    ← задание «Финальная архитектура ИИ-контура» (14 требований)
│   ├── 01-models-and-migrations.patch
│   ├── 02-indexing.patch
│   ├── 03-rag.patch
│   ├── 04-ui.patch
│   ├── 05-reports.patch
│   ├── 06-tests.patch
│   └── 99-full.patch                          ← все изменения одним файлом
├── sod-hotfix-b7c702b-pdfium-open.patch       ← хотфикс 500 кнопки «Подтвердить» (open_pdf, НОВОЕ в рев. 3)
└── sod-hotfix-8f8b42a-button-markups.patch    ← хотфикс маркапов (УЖЕ включён в b7c702b — оставлен как справка)
```

## Базы для применения

| Набор патчей | Базовый коммит |
|---|---|
| `stage20-final-ai-chain/*.patch` | **`b7c702b`** fix(ai_checks): button markups hotfix (текущий HEAD main) |
| `sod-hotfix-b7c702b-pdfium-open.patch` | **`b7c702b`** — применяется и на чистый `b7c702b`, и поверх патчей stage20 |
| `sod-hotfix-8f8b42a-button-markups.patch` | `8f8b42a` — **уже применён и закоммичен как часть `b7c702b`**; на `b7c702b` корректно отказывается накладываться |

История main: `3049a6a` → `e0e2c3a` → `8f8b42a` → `b7c702b`.
Патчи stage20 перегенерированы от `b7c702b`: учтены замена PyMuPDF →
pypdfium2/reportlab (коммит `8f8b42a`) и хотфикс кнопок/маркапов (`b7c702b`).

## Применение

Патчи stage20 и хотфикс `pdfium-open` **не пересекаются по файлам**
(stage20 не трогает `ai_checks/pdfium_tools.py`), поэтому порядок любой.
Рекомендуемая последовательность:

```bash
git checkout b7c702b

# хотфикс кнопки «Подтвердить» (см. раздел ниже)
git apply --whitespace=nowarn sod-hotfix-b7c702b-pdfium-open.patch

# задание «Финальная архитектура ИИ-контура» — либо по частям:
git apply --whitespace=nowarn stage20-final-ai-chain/01-models-and-migrations.patch
git apply --whitespace=nowarn stage20-final-ai-chain/02-indexing.patch
git apply --whitespace=nowarn stage20-final-ai-chain/03-rag.patch
git apply --whitespace=nowarn stage20-final-ai-chain/04-ui.patch
git apply --whitespace=nowarn stage20-final-ai-chain/05-reports.patch
git apply --whitespace=nowarn stage20-final-ai-chain/06-tests.patch
# либо одним файлом (эквивалентно 01–06):
git apply --whitespace=nowarn stage20-final-ai-chain/99-full.patch

python manage.py migrate   # новые: ai_checks.0010_…, storage.0030_…
```

## Хотфикс `sod-hotfix-b7c702b-pdfium-open.patch` (диагностика и фикс)

**Симптом.** После замены PyMuPDF на pypdfium2 кнопка «Подтвердить»
находку возвращает 500: `pypdfium2._helpers.misc.PdfiumError: Failed to
load document (PDFium: Data format error)` в
`ai_checks/pdfium_tools.py::open_pdf`, при том что файл — корректный PDF,
который PyMuPDF открывал.

**Диагностика (зондами, pypdfium2 5.7.0).**

1. Чекпойнт аудита «строка пути vs bytes»: `PdfDocument(str/Path)`
   в pypdfium2 5.7.0 поддерживается нативно — на корректных PDF путь
   открывается без ошибок (проверено на 4 вариантах повреждений).
2. Реальный класс отказа воспроизведён: **PDF без xref-таблицы**
   (файл, обрезанный при передаче/сохранении — например, после
   недокачанного аплоада). PyMuPDF/MuPDF такие файлы чинит молча
   (pdf_repair_xref), PDFium — нет: ровно `FPDF_ERR_FORMAT` =
   «Data format error». Все прочие повреждения (битые смещения xref,
   startxref в объект, лишний мусор до `%PDF-`, CRLF, отсутствие
   `%%EOF`) PDFium переживает сам.
3. Чекпойнт «file-like должен оставаться открытым»: при загрузке из
   потока pypdfium2 действительно требует живой handle; при загрузке
   из bytes буфер удерживается самим документом.
4. Вызов в `bbox_ratios_for_rotation` корректен (передаётся путь
   `version.file.path` из `confirm_finding`), но ошибка открытия
   приводила к 500 без запасной ветки.

**Фикс (2 файла + новый тест).**

- `ai_checks/pdfium_tools.py`:
  - `open_pdf` читает источник в bytes **до** передачи в pypdfium2:
    str/Path — через `Path.read_bytes()` (аналог `open(path,'rb').read()`;
    нативный `FPDF_LoadDocument` больше не используется — кодировка пути
    и второе открытие файла исключены; отсутствующий файл даёт понятное
    сообщение с путём); file-like — читается целиком, поэтому держать
    дескриптор открытым не нужно; bytes/ctypes — как есть. Файл в
    текстовом режиме даёт явное «нужен open(path, "rb")».
  - Авто-ремонт: при `FPDF_ERR_FORMAT` выполняется `_rebuild_xref()` —
    пересборка xref-таблицы по фактическому расположению объектов
    `N G obj` (аналог pdf_repair_xref MuPDF) с поиском `/Type /Catalog`
    (если каталога нет, но есть `/Type /Pages` — Catalog синтезируется),
    затем повторное открытие восстановленной копии. На валидных файлах
    оверхед нулевой — ремонт запускается только при ошибке разбора.
- `ai_checks/domain/services.py`: `bbox_ratios_for_rotation` при
  `PdfiumError` возвращает `{}` вместо 500 — подтверждение находки
  деградирует к маркапу по центру (view уже подставляет 0.5),
  кроп не строится (его ветка и раньше была защищена).
- `ai_checks/tests/test_pdfium_open.py` (новый, 12 тестов): путь/bytes/
  file-like/текстовый режим/отсутствующий файл; обрезанный PDF без xref
  чинится и по пути, и по bytes (текст извлекается, bbox считается);
  пустой файл и мусор без `%PDF-` дают понятное `PdfiumError`;
  `bbox_ratios_for_rotation` на неоткрываемом файле возвращает `{}`.

## Покрытие требований (stage20, 14 пунктов)

| Пункт | Требование | Где реализовано |
|---|---|---|
| 1 | `ProjectDocumentChunk` (FK document/version/section/folder, seq, text, page_from/page_to, embedding 1024, UniqueConstraint(document, version, seq)) + миграция | `ai_checks/domain/models.py`, `ai_checks/migrations/0010_…` |
| 2 | `RagQuery` (user, project, question, answer, sources JSON, model, created_at; индекс (project, −created_at)) + миграция | там же |
| 3 | `indexing_status` (pending/processing/done/error) у `DocumentVersion` и `InternalDocument` + миграции | `storage/domain/models.py`, `storage/migrations/0030_…`; переименование `index_status`→`indexing_status` с конвертацией значений в `0010_…` |
| 4 | Автоиндексация: `upload_document()` при `AI_CHECKS_ENABLED=True` ставит Celery-задачу `index_project_document(version_id)`; задача: снос старых чанков → разбор PDF → чанки + векторы → статусы | `storage/domain/services.py`, `ai_checks/tasks.py`, `ai_checks/domain/services.py::index_project_document` |
| 5 | `reindex_project(project_id, user)` и `reindex_document(document_id, user)` | `ai_checks/domain/services.py` |
| 6 | `rag_answer(question, project_id, user, section_code=None, source_type=None, model=None)`: поиск по `ProjectDocumentChunk` + `InternalChunk` (tenant_scope), объединение top_k=5, LLM-синтез с цитатами, запись в `RagQuery`, формат sources `[{type, document_title, section_code, page, text, score}]` | `ai_checks/domain/services.py::rag_answer`, `ai_checks/adapters/vector_pg.py::ProjectChunkVectorBackend` |
| 7 | Вкладка «База знаний»: таблица «Документы проекта» + кнопка «Переиндексировать проект»; таблица «Внутренние стандарты» + кнопка «Загрузить стандарт» | `ai_checks/views.py` (kb_*), `templates/ai_checks/_kb.html`, `_kb_projects.html`, `_kb_table.html` |
| 8 | ИИ-поиск: фильтры (раздел ОВ/ЭОМ/АР, тип источника проект/стандарт, модель), выбор модели, источники у результатов, чип-пример «Какие нагрузки из ОВ учтены в ЭОМ?» | `templates/ai_checks/_rag.html`, `_rag_results.html` |
| 9 | «История запросов» под формой (последние 20): дата / автор / вопрос / модель, клик — разворот ответа и источников; видна всем участникам проекта | `templates/ai_checks/_rag_history.html`, `_rag_history_detail.html`, вьюхи `rag_history`, `rag_history_item` |
| 10 | Поиск по истории: вопрос + автор + даты, HTMX, пагинация | там же |
| 11 | Отчёт ГИПу: payload `total_issues / by_status / overdue_count / by_assignee / top_issues`, промпт для ГИПа без слова «находки», сохранение `GeneratedReport` | `ai_checks/domain/services.py::generate_gip_report` |
| 12 | Экспорт PDF + Word: заголовок, таблица замечаний, сводка, «Выводы» | `gip_report_pdf` (reportlab — pypdfium2-стек), `gip_report_docx`, `_report_conclusions` |
| 13 | История отчётов: дата / период / автор / кнопки PDF и Word; видна только ГИП/админу | `templates/ai_checks/_report_history.html`, вьюха `report_panel`, `_report_allowed` |
| 14 | `AI_AVAILABLE_MODELS` = `deepseek/deepseek-v4.1-flash-free`, `z-ai/glm-5.3-flash-free`; параметр model передаётся в LLMProvider; при сбое LLM — «Резюме недоступно (ошибка LLM)» | `sod_core/settings.py`, `_selected_model`, `LLM_ERROR_SUMMARY` в services.py |

## Адаптация к стеку b7c702b (pypdfium2 вместо PyMuPDF)

- `index_project_document` использует `extract_pages()` из services.py —
  в стеке b7c702b это обёртка над `ai_checks.pdfium_tools` (pypdfium2);
  сам код индексации не изменился.
- Раздел «Выводы» (треб. 12) в PDF-экспорте встроен в reportlab-версию
  `gip_report_pdf` (до `canvas.save()`); в Word — как раньше, через
  python-docx.
- Тестовые фикстуры stage20 переведены с PyMuPDF на reportlab
  (`make_pdf` в `test_project_indexing.py` — `pdfgen.cyr_font()` +
  `simpleSplit` с переносом внутри рамки, как прежний `insert_textbox`;
  в `test_views_stage20.py` убран неиспользуемый `import pymupdf`),
  т.к. PyMuPDF больше нет в requirements.txt.
- Конфликты слияния (импорты `services.py`/`views.py`, хвост
  `gip_report_pdf`) разрешены вручную: правки хотфикса
  (`pdfium_tools`, `@never_cache`) сохранены, «Выводы» встроены в
  reportlab-версию рендера.

## Верификация (2026-11-08, база b7c702b)

- Окружение: Python 3.12, Django, PostgreSQL 16 + pgvector;
  **pymupdf удалён из окружения** — тесты идут строго по
  requirements.txt (`pypdfium2==5.7.0`, `reportlab==4.4.9`, `pypdf==6.6.0`).
- Тесты `ai_checks` на финальном дереве (b7c702b + stage20 + хотфикс):
  **141 тест, OK** (1 skip — ожидаемый): 129 тестов stage20 +
  12 регрессионных тестов хотфикса `test_pdfium_open`.
- `storage`: 362 теста, 1 падение
  (`test_project_detail_contractor_sees_only_allowed_documents`)
  воспроизводится и на чистом `b7c702b` без патчей — предсуществующая
  проблема, к патчам не относится.
- Применение проверено `git apply` на чистых worktree `b7c702b`:
  - `01…06` последовательно — результат побайтово совпадает с
    протестированным деревом (`diff -r`, без расхождений);
  - `99-full.patch` — то же самое;
  - хотфикс `pdfium-open` — на чистом `b7c702b` и поверх stage20;
    после него контрольные функции (`bbox_ratios_for_rotation`,
    `pdfium_tools.py`) идентичны эталону.
- `manage.py makemigrations --check` — расхождений моделей и миграций нет.
- Воспроизведение диагностики: зонд на 5 классах повреждений PDF —
  только «обрезанный без xref» даёт «Data format error» у PDFium и
  открывается у MuPDF; после фикса тот же файл открывается, текст и
  bbox корректны.

### Контрольные суммы (sha256)

```
01-models-and-migrations.patch  63ce05436599bbf02c8655eaf1135c0a9e98e7304abeadbd5bb3c6d840ff0d11
02-indexing.patch               086f1ae41f0c201312c4927f9a9d050b35513521dfc4a19f208bd5e67d205abe
03-rag.patch                    03bf3b2b64ea8d377bd4320d0a15af917e537871d693ea90fe19e088997e0f78
04-ui.patch                     589cc9599e2bc08c8f604fb1a482df56d8e3fb551f0ba7a38ff117b9788a2918
05-reports.patch                7142bdeed93c3267fea797ea6df08a28f30f6252dcd8cb17b5d8c8a06c2db637
06-tests.patch                  b59de6679f2390be8ba50cf734a5a3057d68d10727c71102b433855d07dc741b
99-full.patch                   48fe44bcbf2103638eb4c265da2b603a69c00aeaf8c5e1a6d035d698676ec137
sod-hotfix-b7c702b-pdfium-open.patch 3d60a6dbc7c3042a8babb4d2de56f51645d79f0c2c0bf9c47a40ffe3c727218a
```

## Исправленный flaky-тест (входит в 06-tests.patch)

`test_reports.GipReportTests.test_report_aggregates_issues` и
`test_model_selection` падали после полуночи TIME_ZONE (Europe/Moscow):
период задавался UTC-датой `timezone.now().date()`, а `_report_issues`
сравнивает её с `created_at__date`, которую ORM считает в локальном
времени — после 00:00 MSK `created_at__date = UTC-дата + 1 > period_end`,
`total_issues = 0`. Воспроизводится на чистом `b7c702b` в соответствующие
часы. Исправлено: границы периода через `timezone.localdate()` (как в
production-вьюхе `report_panel`).

## Замечания по совместимости

- Векторный поиск требует PostgreSQL + pgvector; на SQLite `rag_answer`
  честно поднимает `NotImplementedError` с подсказкой поднять dev-стек
  (docker-compose.dev.yml).
- Документы базы знаний (`KB-*`) не попадают в проектный индекс и таблицу
  «Документы проекта» — они индексируются в `InternalChunk` (разделение
  источников «проект» / «стандарт» по треб. 6).
- Ремонт в `open_pdf` восстанавливает структуру, но не содержимое
  потерянного хвоста файла: если при передаче потеряны страницы,
  документ откроется, однако текст может быть неполным — такие случаи
  видны в логах как `PdfiumError`/repair и требуют перекачки файла.
