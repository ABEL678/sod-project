# Stream-CAD — сводный пакет патчей (unified diff)

Дата сборки: 2026-11-08. Репозиторий: github.com/ABEL678/sod-project (main).

Состав — патчи по двум заданиям:

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
└── hotfix-8f8b42a-button-markups.patch        ← предыдущее задание (хотфикс разметки кнопок)
```

## Базы для применения

| Набор патчей | Базовый коммит |
|---|---|
| `stage20-final-ai-chain/*.patch` | `e0e2c3a` fix(ai_checks): исправлен ассерт test_kb_panel_renders |
| `hotfix-8f8b42a-button-markups.patch` | `8f8b42a` refactor(pdf): замена PyMuPDF на pypdfium2 + reportlab + pypdf |

Порядок коммитов в репозитории: `3049a6a` → `e0e2c3a` → `8f8b42a`.
Наборы патчей независимы: каждый накладывается на свою базу.

## Применение

```bash
# 1) Задание с 14 требованиями — на базу e0e2c3a
git checkout e0e2c3a
git apply --whitespace=nowarn 01-models-and-migrations.patch
git apply --whitespace=nowarn 02-indexing.patch
git apply --whitespace=nowarn 03-rag.patch
git apply --whitespace=nowarn 04-ui.patch
git apply --whitespace=nowarn 05-reports.patch
git apply --whitespace=nowarn 06-tests.patch
# либо одним файлом:
git apply --whitespace=nowarn 99-full.patch

# 2) Хотфикс кнопок — на базу 8f8b42a
git checkout 8f8b42a
git apply --whitespace=nowarn hotfix-8f8b42a-button-markups.patch
```

После применения набора stage20 выполнить миграции: `python manage.py migrate`
(новые: `ai_checks.0010_projectdocumentchunk_ragquery_indexing_status`,
`storage.0030_documentversion_indexing_status`).

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
| 12 | Экспорт PDF + Word: заголовок, таблица замечаний, сводка, «Выводы» | `gip_report_pdf`, `gip_report_docx`, `_report_conclusions` |
| 13 | История отчётов: дата / период / автор / кнопки PDF и Word; видна только ГИП/админу | `templates/ai_checks/_report_history.html`, вьюха `report_panel`, `_report_allowed` |
| 14 | `AI_AVAILABLE_MODELS` = `deepseek/deepseek-v4.1-flash-free`, `z-ai/glm-5.3-flash-free`; параметр model передаётся в LLMProvider; при сбое LLM — «Резюме недоступно (ошибка LLM)» | `sod_core/settings.py`, `_selected_model`, `LLM_ERROR_SUMMARY` в services.py |

## Верификация (2026-11-08)

- Тесты `ai_checks` на наборе stage20 (PostgreSQL 16 + pgvector): **129 тестов, OK** (1 skip — ожидаемый).
- `storage`: 361 из 362 тестов проходит; единственный падающий
  `test_project_detail_contractor_sees_only_allowed_documents` воспроизводится
  и на чистой базе `e0e2c3a` без патчей — предсуществующая проблема, к патчам
  не относится.
- Применение проверено `git apply` на чистых worktree:
  - `01…06` последовательно на `e0e2c3a` — применяется, результат побайтово
    совпадает с протестированной рабочей копией;
  - `hotfix-8f8b42a-button-markups.patch` на `8f8b42a` — применяется,
    результат совпадает с рабочей копией.
- Миграции применяются на PostgreSQL (расширение `vector` создано ранее
  зависимой миграцией `0003_hnsw_index`); на SQLite HNSW-индекс — no-op.

## Замечания по совместимости

- Набор stage20 ожидает `pgvector` и PostgreSQL для векторного поиска;
  на SQLite `rag_answer` честно поднимает `NotImplementedError` с подсказкой
  поднять dev-стек (docker-compose.dev.yml).
- Документы базы знаний (`KB-*`) не попадают в проектный индекс и таблицу
  «Документы проекта» — они индексируются в `InternalChunk` (разделение
  источников «проект» / «стандарт» по треб. 6).
