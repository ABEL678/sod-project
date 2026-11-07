# Патч документации Stream-CAD (СОД) v1.5 → v1.6 «ИИ-контур (ai_checks)»

- Роль: Аудитор. Дата: 06.11.2026
- Основание: ТЗ «Корректировка Плана RAG + LLM» (решение владельца продукта), Инструкция для Z.ai, HANDOFF.md
- База патча: **origin/main, коммит `231842b`** («docs: Аудит 6.0 (v2) и задачи 8–11b»), github.com/ABEL678/sod-project
- Формат: `git diff --binary` (по Инструкции), применение через `git apply`

## Состав поставки

| Файл/каталог | Назначение |
|---|---|
| `stream-cad_docs_v1.6.patch` | Единый патч на `docs/ARCHITECTURE.md`, `docs/MASTER_SPEC.md`, `docs/PROGRESS.md` |
| `docs-v1.6/` | Результирующие файлы после применения (для просмотра без патча) |

## Применение

```bash
cd sod-project                       # репозиторий на main (231842b или новее)
git apply --check stream-cad_docs_v1.6.patch   # проверка
git apply stream-cad_docs_v1.6.patch           # применение
git add docs/ && git commit -m "docs: v1.6 ИИ-контур (ai_checks) — корректировка плана RAG+LLM"
```

## Что меняет патч (237 вставок / 10 удалений, 14 hunks, 3 файла в docs/)

**docs/ARCHITECTURE.md (1.5 → 1.6):**
- Шапка: версия 1.6, дата 06.11.2026, статус.
- 2.1: в структуру добавлено `ai_checks/`; у `ai_assistant/` помечено «вне MVP Этапа 19».
- 2.3 и 7 (правило 4): исключение для ИИ-контура из запрета Celery/Redis/pgvector.
- 3.2: новые модели — InternalDocument, InternalChunk (переименованы из NormDocument/NormChunk), RuleDefinition, CheckRun, PendingCheckResult, LLMCallLog; tenant_scope/company/project; правило приватности промптов.
- 3.3: связи ИИ-контура.
- 5.3–5.5: порты LLMProvider, EmbeddingService, VectorBackend в `ai_checks/ports/`; PgVectorBackend — единственная реализация.
- Новый раздел 8 «ИИ-контур (ai_checks)»: фича-флаг `AI_CHECKS_ENABLED=False`, мультитенантность `tenant_scope = global | company | project`, инфраструктура dev = prod (docker-compose.dev.yml: pgvector/pgvector:pg16 + redis:7-alpine; прод: существующий PostgreSQL + CREATE EXTENSION vector; без sqlite-vec/NullVectorBackend), конвейер проверок, User Flow (HANDOFF №8), RAG по нормам отложен (on-premise опция), VLM (Ф3) в бэклоге.

**docs/MASTER_SPEC.md (1.5 → 1.6):**
- Шапка; блок «#### 19. T67» перенумерован в «#### 18.10» (устранён конфликт с новым Этапом 19).
- Новый **Этап 19 «ИИ-контур ai_checks»** (MVP ≈ 13 недель): ключевые решения, фазы Ф0.1/Ф0.2/Ф1/Ф2/Ф4 + Ф3 (бэклог) с составами, сроками и DoD; явно зафиксировано «RAG по нормам — On-premise опция (ручная загрузка 5–10 ГОСТ/СП), не SaaS» и «Ф3 — отложено до накопления эталона 30–50 размеченных листов»; пост-MVP.
- Раздел 5 (ограничения): Celery/Redis/pgvector — только в рамках Этапа 19.
- Раздел 6: строка Этапа 19 в таблице контрольных точек.

**docs/PROGRESS.md:**
- «Текущий этап»: решение по нормативной базе (смена фокуса, не тупик), инфраструктура, Ф3 в бэклоге; **следующий шаг — Ф0.1** (инфраструктура + абстракции + LLMCallLog; зависимости в requirements.txt — в ТЗ Кодеру на Ф0.1).
- Журнал: запись «06.11.2026 — Корректировка плана RAG + LLM».
- Бэклог: блок «ИИ-контур (Этап 19)» — Ф0.1, Ф3, RAG по нормам (пост-MVP).

## Ограничения соблюдены

- Патч трогает только 3 файла в `docs/` — код, `requirements.txt`, миграции не затронуты.
- Существующие модели (Document, DocumentVersion, Issue, Notification и др.) не изменяются — только добавление новых в `ai_checks` (зафиксировано в ARCHITECTURE.md 3.2 и 8).

## Верификация (выполнена перед передачей)

1. `git apply --check` на чистом клоне origin/main `231842b` — без конфликтов.
2. `git apply` — применён; результат побайтно совпадает с `docs-v1.6/`.
3. Состав — ровно 3 файла `.md` в `docs/`.
4. Если main уйдёт вперёд с момента 231842b и хунты не лягут — сообщите, перегенерирую от нового HEAD за один проход.
