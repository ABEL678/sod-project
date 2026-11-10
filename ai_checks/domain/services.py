"""Домен ИИ-контура: индексация, RAG-поиск, база знаний, отчёт ГИПу.

Контракт (ARCHITECTURE.md 8.4): pypdfium2 извлекает текст и страницы,
текст режется на чанки с перекрытием, векторы пишутся в InternalChunk /
ProjectDocumentChunk. Эмбеддинги по умолчанию — облако Сбера
(GigaChat, адаптер embedding_gigachat.get_embedding_service;
офлайн-фолбэк — bge-m3 ONNX). Сервис не знает про HTTP/UI/
Celery — только домен и ORM.

Состав (этап 20, после зачистки нормоконтроля 2026-11-09):
- index_document() / index_project_document() — индексация базы знаний и
  PDF-версий документов проекта (треб. 1, 3, 4);
- reindex_project() / reindex_document() — резервная индексация (треб. 5);
- rag_answer() — сквозной RAG по документам проекта и базе знаний с
  сохранением истории RagQuery (треб. 6, 9);
- generate_gip_report() + экспорт PDF/Word с разделом «Выводы» (треб. 12);
- база знаний: upload/remove/knowledge_documents (UX-аудит stage 2).
ИИ-нормоконтроль СПДС (rules.py, run_rules_check, прогоны CheckRun)
выведен из эксплуатации и удалён — см. миграцию 0013.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied
from django.db import models as db_models
from django.db import transaction
from django.utils import timezone

from ai_checks import pdfium_tools
from ai_checks.domain.models import (
    GeneratedReport,
    IndexingStatus,
    InternalChunk,
    InternalDocument,
    LLMCallLog,
    ProjectDocumentChunk,
    RagQuery,
)
from ai_checks.ports.embedding import EmbeddingService
from storage.domain.models import Document, DocumentVersion

logger = logging.getLogger(__name__)

# Размер чанка и перекрытие (ТЗ Ф0.2: ~1000 символов, перекрытие 200).
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

# Документы базы знаний создаются через upload_document с шифром KB-*;
# в проектной таблице «Документы проекта» и переиндексации проекта они
# не участвуют (они индексируются в InternalChunk как стандарты).
KB_CODE_PREFIX = 'KB-'


@dataclass
class TextChunk:
    """Фрагмент текста с границами страниц."""

    text: str
    page_from: int | None
    page_to: int | None


def extract_pages(file_path: str) -> list[str]:
    """Извлекает текст постранично из PDF (pypdfium2)."""
    with pdfium_tools.open_pdf(file_path) as pdf:
        pages: list[str] = []
        for index in range(len(pdf)):
            view = pdfium_tools.PdfPageView(pdf, index)
            try:
                pages.append(view.get_text())
            finally:
                view.close()
        return pages


def split_into_chunks(
    pages: list[str],
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[TextChunk]:
    """Режет текст PDF на чанки с перекрытием, сохраняя страницы.

    Страницы склеиваются через '\\n'; для каждого чанка page_from/page_to
    определяются по пересечению с диапазонами страниц.
    """
    if chunk_size <= 0:
        raise ValueError('chunk_size должен быть положительным')
    overlap = max(0, min(overlap, chunk_size - 1))

    parts: list[str] = []
    page_ranges: list[tuple[int, int, int]] = []  # (start, end, page_no)
    position = 0
    for page_no, text in enumerate(pages, start=1):
        page_ranges.append((position, position + len(text), page_no))
        parts.append(text)
        position += len(text) + 1  # + '\n'

    full_text = '\n'.join(parts)
    step = chunk_size - overlap
    chunks: list[TextChunk] = []
    start = 0
    while start < len(full_text):
        end = min(start + chunk_size, len(full_text))
        piece = full_text[start:end]
        if piece.strip():
            chunk_pages = [
                page_no
                for (page_start, page_end, page_no) in page_ranges
                if page_start < end and page_end > start
            ]
            chunks.append(TextChunk(
                text=piece,
                page_from=min(chunk_pages) if chunk_pages else None,
                page_to=max(chunk_pages) if chunk_pages else None,
            ))
        if end >= len(full_text):
            break
        start += step
    return chunks


def index_document(
    internal_doc: InternalDocument,
    file_path: str,
    embedding_service: EmbeddingService | None = None,
) -> int:
    """Индексирует PDF: текст → чанки → векторы → InternalChunk.

    Повторный вызов заменяет прежние фрагменты (идемпотентно) и обновляет
    embedding_model/indexed_at. Возвращает количество созданных чанков.
    """
    if not file_path:
        raise ValueError('Не указан путь к файлу PDF')

    if embedding_service is None:
        from ai_checks.adapters.embedding_factory import (
            get_embedding_service,
        )

        embedding_service = get_embedding_service()

    chunks = split_into_chunks(extract_pages(file_path))
    if not chunks:
        with transaction.atomic():
            internal_doc.chunks.all().delete()
            internal_doc.embedding_model = embedding_service.model_version
            internal_doc.indexed_at = timezone.now()
            internal_doc.save(update_fields=['embedding_model', 'indexed_at'])
        return 0

    vectors = embedding_service.embed_texts([chunk.text for chunk in chunks])
    if len(vectors) != len(chunks):
        raise ValueError(
            'EmbeddingService вернул неверное число векторов: '
            f'{len(vectors)} != {len(chunks)}'
        )
    dimension = embedding_service.dim
    for vector in vectors:
        if len(vector) != dimension:
            raise ValueError(
                'Размерность вектора не совпадает с EmbeddingService.dim: '
                f'{len(vector)} != {dimension}'
            )

    with transaction.atomic():
        internal_doc.chunks.all().delete()
        InternalChunk.objects.bulk_create([
            InternalChunk(
                document=internal_doc,
                seq=seq,
                text=chunk.text,
                page_from=chunk.page_from,
                page_to=chunk.page_to,
                embedding=vector,
            )
            for seq, (chunk, vector) in enumerate(zip(chunks, vectors))
        ])
        internal_doc.embedding_model = embedding_service.model_version
        internal_doc.indexed_at = timezone.now()
        internal_doc.save(update_fields=['embedding_model', 'indexed_at'])
    return len(chunks)


def index_project_document(
    version_id: int,
    embedding_service: EmbeddingService | None = None,
) -> int:
    """Индексация PDF-версии документа проекта (треб. 4).

    Удаляет прежние ProjectDocumentChunk документа, режет PDF на чанки,
    считает векторы и сохраняет фрагменты со снимком раздела/папки.
    Статус версии (треб. 3): pending → processing → done/error.
    Идемпотентен: повторный вызов заменяет фрагменты. Сбой не роняет
    Celery-задачу — версия помечается error (текст в логе приложения).
    Возвращает число созданных чанков (0 — версия/документ не найдены).
    """
    try:
        version = (
            DocumentVersion.objects
            .select_related('document', 'document__section', 'document__folder')
            .get(pk=version_id)
        )
    except DocumentVersion.DoesNotExist:
        logger.warning('Индексация: версия %s не найдена', version_id)
        return 0

    version.indexing_status = IndexingStatus.PROCESSING
    version.save(update_fields=['indexing_status'])
    try:
        if embedding_service is None:
            from ai_checks.adapters.embedding_factory import (
                get_embedding_service,
            )

            embedding_service = get_embedding_service()

        chunks = split_into_chunks(extract_pages(version.file.path))
        vectors: list[list[float]] = []
        if chunks:
            vectors = embedding_service.embed_texts(
                [chunk.text for chunk in chunks],
            )
        if len(vectors) != len(chunks):
            raise ValueError(
                'EmbeddingService вернул неверное число векторов: '
                f'{len(vectors)} != {len(chunks)}'
            )
        dimension = embedding_service.dim
        for vector in vectors:
            if len(vector) != dimension:
                raise ValueError(
                    'Размерность вектора не совпадает с EmbeddingService.dim: '
                    f'{len(vector)} != {dimension}'
                )

        with transaction.atomic():
            # Старые чанки документа (в т.ч. от прошлых версий) сносим:
            # актуальный контекст — только проиндексированная версия.
            ProjectDocumentChunk.objects.filter(
                document=version.document,
            ).delete()
            if chunks:
                ProjectDocumentChunk.objects.bulk_create([
                    ProjectDocumentChunk(
                        document=version.document,
                        version=version,
                        section=version.document.section,
                        folder=version.document.folder,
                        seq=seq,
                        text=chunk.text,
                        page_from=chunk.page_from,
                        page_to=chunk.page_to,
                        embedding=vector,
                    )
                    for seq, (chunk, vector) in enumerate(zip(chunks, vectors))
                ])
            version.indexing_status = IndexingStatus.DONE
            version.save(update_fields=['indexing_status'])
        return len(chunks)
    except Exception as exc:  # noqa: BLE001 — статус версии важнее трейса
        logger.exception(
            'Индексация версии %s упала: %s', version_id, exc,
        )
        version.indexing_status = IndexingStatus.ERROR
        version.save(update_fields=['indexing_status'])
        return 0


def reindex_document(document_id: int, user=None) -> int:
    """Резервная индексация одного документа проекта (треб. 5).

    Ставит текущей версии документа статус pending и задачу Celery.
    user — инициатор (используется вьюхой для проверки прав; сервис
    дополнительно не пускает удалённые документы). Возвращает id версии
    (0 — индексировать нечего: нет документа/версии или это KB-*).
    """
    from ai_checks.tasks import index_project_document_task

    version = (
        DocumentVersion.objects
        .filter(
            document_id=document_id,
            document__is_deleted=False,
            document__current_version=db_models.F('pk'),
        )
        .exclude(document__code__startswith=KB_CODE_PREFIX)
        .first()
    )
    if version is None:
        return 0
    version.indexing_status = IndexingStatus.PENDING
    version.save(update_fields=['indexing_status'])
    index_project_document_task.delay(version.pk)
    return version.pk


def reindex_project(project_id: int, user=None) -> int:
    """Резервная индексация всех документов проекта (треб. 5).

    Все неудалённые документы проекта с текущей PDF-версией получают
    статус pending и задачу index_project_document. user — инициатор:
    если задан, сервис fail-closed перепроверяет доступ к проекту
    (вьюха проверяет права отдельно). Возвращает число поставленных
    задач (не PDF и KB-* — пропускаются).
    """
    from ai_checks.tasks import index_project_document_task

    if user is not None:
        from storage.domain.services import get_accessible_projects

        if not get_accessible_projects(user).filter(pk=project_id).exists():
            raise PermissionDenied('Нет доступа к проекту')

    versions = list(
        DocumentVersion.objects
        .filter(
            document__project_id=project_id,
            document__is_deleted=False,
            document__current_version=db_models.F('pk'),
        )
        .exclude(document__code__startswith=KB_CODE_PREFIX)
        .order_by('document__code')
    )
    if not versions:
        return 0
    DocumentVersion.objects.filter(
        pk__in=[version.pk for version in versions],
    ).update(indexing_status=IndexingStatus.PENDING)
    for version in versions:
        index_project_document_task.delay(version.pk)
    return len(versions)


def rag_answer(
    question: str,
    project_id: int | None = None,
    user=None,
    company_id: int | None = None,
    section_code: str | None = None,
    source_type: str | None = None,
    model: str | None = None,
    top_k: int = 5,
    embedding_service: EmbeddingService | None = None,
    vector_backend=None,
    project_backend=None,
    llm_provider=None,
) -> dict:
    """Сквозной RAG-ответ: документы проекта + база знаний (треб. 6).

    Вопрос → эмбеддинг → поиск по ProjectDocumentChunk (project_id,
    опционально section_code) и по InternalChunk (tenant_scope 8.2:
    project/company + глобальные) → объединение, сортировка по score,
    top_k → LLM-синтез с цитатами [N] → запись RagQuery (история,
    треб. 9). Возвращает {answer, sources}.

    Формат sources (треб. 6): [{type, document_title, section_code,
    page, text, score}]; дополнительно chunk_id/tenant_scope для UI.
    type: 'project' — PDF-документы проекта, 'standard' — база знаний.
    source_type: None — все источники, 'project'/'standard' — один блок.

    DI-параметры (embedding_service/vector_backend/project_backend/
    llm_provider) — для тестов и смены адаптеров; company_id можно
    передать явно (иначе берётся из проекта). SQLite-режим: проектный
    поиск недоступен (pgvector), внутренний — через mock-бэкенд тестов.
    """
    question = (question or '').strip()
    if not question:
        raise ValueError('Пустой вопрос')
    if source_type not in (None, 'project', 'standard'):
        raise ValueError('Тип источника: project или standard')

    if embedding_service is None:
        from ai_checks.adapters.embedding_factory import (
            get_embedding_service,
        )

        embedding_service = get_embedding_service()

    # company_id: явный аргумент (совместимость со старыми вызовами) или
    # компания проекта — для tenant-фильтра базы знаний (8.2).
    if company_id is None and project_id is not None:
        company_id = (
            Document.objects
            .filter(project_id=project_id)
            .values_list('project__company_id', flat=True)
            .first()
        )

    vectors = embedding_service.embed_texts([question])
    if not vectors:
        raise ValueError('EmbeddingService не вернул вектор вопроса')
    question_vector = vectors[0]

    merged_hits: list[dict] = []  # {source_type, hit}
    project_search_failed = False
    internal_search_failed = False

    # 1) Документы проекта (ProjectDocumentChunk, треб. 6).
    if source_type in (None, 'project') and project_id is not None:
        if project_backend is None:
            from ai_checks.adapters.vector_pg import ProjectChunkVectorBackend

            project_backend = ProjectChunkVectorBackend(
                dimension=embedding_service.dim,
            )
        try:
            project_hits = project_backend.search(
                question_vector,
                {
                    'project_id': project_id,
                    'section_code': section_code,
                },
                top_k,
            )
        except NotImplementedError:
            project_search_failed = True
            project_hits = []
        merged_hits.extend(
            {'source_type': 'project', 'hit': hit}
            for hit in project_hits
        )

    # 2) База знаний (InternalChunk, tenant-фильтры 8.2).
    if source_type in (None, 'standard'):
        if vector_backend is None:
            from ai_checks.adapters.vector_pg import PgVectorBackend

            vector_backend = PgVectorBackend(dimension=embedding_service.dim)
        filters = {
            'company_id': company_id,
            'project_id': project_id,
        }
        try:
            internal_hits = vector_backend.search(
                question_vector, filters, top_k,
            )
        except NotImplementedError:
            internal_search_failed = True
            internal_hits = []
        merged_hits.extend(
            {'source_type': 'standard', 'hit': hit}
            for hit in internal_hits
        )

    # Треб. 6: объединить, отсортировать по score, взять top_k.
    merged_hits.sort(
        key=lambda entry: entry['hit'].get('score') or 0.0,
        reverse=True,
    )
    merged_hits = merged_hits[:max(1, int(top_k or 1))]

    sources = []
    for entry in merged_hits:
        hit = entry['hit']
        if entry['source_type'] == 'project':
            chunk = (
                ProjectDocumentChunk.objects
                .select_related('document', 'section', 'version')
                .filter(pk=hit['id'])
                .first()
            )
            if chunk is None:
                continue
            sources.append({
                'type': 'project',
                'chunk_id': chunk.pk,
                'document_title': (
                    f'{chunk.document.code} — {chunk.document.name}'
                ),
                'section_code': (
                    chunk.section.code if chunk.section_id else None
                ),
                'page': hit.get('page_from'),
                'text': chunk.text,
                'score': hit.get('score'),
                'tenant_scope': 'project',
            })
        else:
            chunk = (
                InternalChunk.objects
                .select_related('document')
                .filter(pk=hit['id'])
                .first()
            )
            if chunk is None:
                continue
            sources.append({
                'type': 'standard',
                'chunk_id': chunk.pk,
                'document_title': chunk.document.title,
                'section_code': None,
                'page': hit.get('page_from'),
                'text': chunk.text,
                'score': hit.get('score'),
                'tenant_scope': chunk.document.tenant_scope,
            })

    answer = 'Ничего не найдено'
    # Модель для истории (RagQuery.model): LLM вызывается только при
    # наличии источников, поэтому used_model инициализируем сразу —
    # запрошенной моделью (UI, треб. 14). Иначе пустой результат поиска
    # падал с UnboundLocalError на used_model[:100] при сохранении
    # RagQuery (регрессия коммита 0951fd8, хотфикс 2026-11-10).
    used_model = model or ''
    if sources:
        # Формируем контекст для LLM-синтеза.
        source_lines = []
        for index, source in enumerate(sources, start=1):
            meta_parts = []
            if source.get('section_code'):
                meta_parts.append(f'раздел {source["section_code"]}')
            if source.get('page'):
                meta_parts.append(f'лист {source["page"]}')
            meta = f' ({", ".join(meta_parts)})' if meta_parts else ''
            source_lines.append(
                f'[{index}] {source["document_title"]}{meta}:\n'
                f'{source["text"][:600]}'
            )
        context_text = '\n\n'.join(source_lines)

        # LLM-синтез ответа с цитатами (модель выбирается в UI, треб. 14).
        if llm_provider is None:
            from ai_checks.adapters.llm_http import get_llm_provider

            llm_provider = get_llm_provider(model=model)

        try:
            response = llm_provider.complete([
                {
                    'role': 'system',
                    'content': (
                        'Ты — помощник ГИПа. Ответь на вопрос, используя '
                        'ТОЛЬКО предоставленные фрагменты документов. '
                        'Формируй связный ответ — не просто склейку цитат. '
                        'Указывай, откуда взята информация '
                        '(документ, лист, раздел). '
                        'Не выдумывай факты и числа. '
                        'Если ответа нет в фрагментах — скажи об этом.'
                    ),
                },
                {
                    'role': 'user',
                    'content': (
                        f'Вопрос: {question}\n\n'
                        f'Фрагменты документов:\n{context_text}'
                    ),
                },
            ], model=model)
            answer = response.text
            used_model = response.model or model or ''
            LLMCallLog.objects.create(
                provider=getattr(llm_provider, 'name', 'unknown'),
                model=response.model or 'unknown',
                purpose=LLMCallLog.Purpose.RAG_ANSWER,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                latency_ms=response.latency_ms,
                status=LLMCallLog.Status.OK,
                prompt_version=RAG_PROMPT_VERSION,
                user=user,
            )
        except Exception as exc:  # noqa: BLE001 — поиск важнее сбоя LLM
            logger.exception('LLM для RAG-ответа упал: %s', exc)
            used_model = model or getattr(llm_provider, 'model', '') or ''
            LLMCallLog.objects.create(
                provider=getattr(llm_provider, 'name', 'unknown'),
                model=getattr(llm_provider, 'model', 'unknown'),
                purpose=LLMCallLog.Purpose.RAG_ANSWER,
                status=LLMCallLog.Status.ERROR,
                error_message=str(exc)[:1000],
                prompt_version=RAG_PROMPT_VERSION,
                user=user,
            )
            answer = LLM_ERROR_SUMMARY

    # Треб. 6/9: запрос попадает в историю (RagQuery) — её видят все
    # участники проекта; пользователь может быть анонимным (API) → null.
    if project_search_failed and internal_search_failed:
        # Оба векторных пути недоступны (например, SQLite-режим) —
        # честно отдаём ошибку вместо пустого «Ничего не найдено».
        raise NotImplementedError(
            'Векторный поиск доступен только на PostgreSQL. '
            'Поднимите dev-стек: docker compose -f docker-compose.dev.yml up -d.'
        )
    RagQuery.objects.create(
        user=user if getattr(user, 'is_authenticated', user is not None) else None,
        project_id=project_id,
        question=question,
        answer=answer,
        sources=sources,
        model=used_model[:100],
    )

    return {'answer': answer, 'sources': sources}


GIP_REPORT_PROMPT_VERSION = 'gip-report@2'
RAG_PROMPT_VERSION = 'rag-answer@1'

# Группировка статусов замечаний (Issue) для отчёта ГИПу.
ISSUE_STATUS_GROUPS = {
    'NEW': 'open',
    'REJECTED': 'open',
    'IN_PROGRESS': 'progress',
    'DISCUSSION': 'progress',
    'DONE': 'review',
    'CLOSED': 'closed',
    'CANCELLED': 'closed',
}
ISSUE_GROUP_ORDER = ('open', 'progress', 'review', 'closed')
ISSUE_GROUP_LABELS = {
    'open': 'Открыто',
    'progress': 'В работе',
    'review': 'На проверке',
    'closed': 'Закрыто',
}
ISSUE_STATUS_LABELS = {
    'NEW': 'Новое',
    'IN_PROGRESS': 'Принято в работу',
    'REJECTED': 'Не принято',
    'DISCUSSION': 'К обсуждению',
    'DONE': 'Выполнено',
    'CLOSED': 'Закрыто',
    'CANCELLED': 'Аннулировано',
}
LLM_ERROR_SUMMARY = 'Резюме недоступно (ошибка LLM)'


def _issue_group(status: str) -> str:
    return ISSUE_STATUS_GROUPS.get(status, 'open')


def _user_label(user) -> str:
    if user is None:
        return 'Без ответственного'
    return user.get_full_name() or user.username


def _report_issues(project, period_start, period_end) -> list:
    """Замечания проекта (Issue) за период — основа отчёта ГИПу."""
    from issues.models import Issue

    return list(
        Issue.objects
        .filter(
            document__project=project,
            document__is_deleted=False,
            created_at__date__gte=period_start,
            created_at__date__lte=period_end,
        )
        .select_related('document', 'assignee')
        .order_by('created_at', 'pk')
    )


def _gip_report_context(project, period_start, period_end, aggregates) -> str:
    lines = [
        f'Проект: {project.name} ({project.code})',
        f'Период: {period_start.isoformat()} — {period_end.isoformat()}',
        f'Всего замечаний: {aggregates["total_issues"]}',
        f'Просрочено: {aggregates["overdue_count"]}',
        'По статусам:',
    ]
    for group in ISSUE_GROUP_ORDER:
        count = aggregates['by_status'].get(group, 0)
        if count:
            lines.append(f'- {ISSUE_GROUP_LABELS[group]}: {count}')
    if aggregates['by_assignee']:
        lines.append('По ответственным:')
        for name, count in list(aggregates['by_assignee'].items())[:15]:
            lines.append(f'- {name}: {count}')
    if aggregates['top_issues']:
        lines.append('Основные проблемы:')
        for issue in aggregates['top_issues']:
            lines.append(
                f'- [{issue["document"]}; {issue["status"]}'
                f'{"; срок " + issue["due_date"] if issue["due_date"] else ""}] '
                f'{issue["message"][:200]}'
            )
    return '\n'.join(lines)


def generate_gip_report(
    project,
    period_start,
    period_end,
    user,
    llm_provider=None,
    model: str | None = None,
) -> GeneratedReport:
    """Сводка ЗАМЕЧАНИЙ проекта для ГИПа за период (Issue, не находки ИИ).

    payload: {total_issues, by_status (open/progress/review/closed),
    overdue_count, by_assignee, by_document, top_issues, summary}.
    Резюме — LLM по агрегатам; при сбое — «Резюме недоступно (ошибка LLM)».
    """
    if period_start > period_end:
        raise ValueError('Начало периода позже конца')

    issues = _report_issues(project, period_start, period_end)
    today = timezone.localdate()

    by_status = {group: 0 for group in ISSUE_GROUP_ORDER}
    by_assignee: dict[str, int] = {}
    by_document: dict[str, int] = {}
    overdue_count = 0
    prioritized = []
    for issue in issues:
        group = _issue_group(issue.status)
        by_status[group] += 1
        assignee_label = _user_label(issue.assignee)
        by_assignee[assignee_label] = by_assignee.get(assignee_label, 0) + 1
        document_code = issue.document.code or issue.document.name
        by_document[document_code] = by_document.get(document_code, 0) + 1
        is_overdue = bool(
            issue.due_date
            and issue.due_date < today
            and issue.status not in ('CLOSED', 'CANCELLED')
        )
        if is_overdue:
            overdue_count += 1
        prioritized.append((
            0 if is_overdue else 1,
            ISSUE_GROUP_ORDER.index(group),
            issue.due_date or today.replace(year=today.year + 100),
            issue.pk,
            issue,
        ))
    prioritized.sort(key=lambda item: item[:4])

    top_issues = []
    # Блок 6: топ-10 ключевых проблем в отчёте (было 7).
    for _overdue, _group, _due, _pk, issue in prioritized[:10]:
        top_issues.append({
            'message': issue.description,
            'document': issue.document.code or issue.document.name,
            'status': ISSUE_STATUS_LABELS.get(
                issue.status, issue.status,
            ),
            'due_date': (
                issue.due_date.isoformat() if issue.due_date else ''
            ),
            'assignee': _user_label(issue.assignee),
            'page': issue.page_number,
        })

    aggregates = {
        'total_issues': len(issues),
        'by_status': by_status,
        'overdue_count': overdue_count,
        'by_assignee': by_assignee,
        'by_document': by_document,
        'top_issues': top_issues,
    }

    if llm_provider is None:
        from ai_checks.adapters.llm_http import get_llm_provider

        llm_provider = get_llm_provider(model=model)

    context_text = _gip_report_context(
        project, period_start, period_end, aggregates,
    )
    llm_call = None
    summary = LLM_ERROR_SUMMARY
    try:
        response = llm_provider.complete([
            {
                'role': 'system',
                'content': (
                    'Ты — помощник ГИПа. Составь краткое деловое резюме для '
                    'ГИПа по замечаниям проекта за период, используя только '
                    'переданные данные. Начни со строк: «Всего замечаний: X. '
                    'Просрочено: Y. В работе: Z.» и перечисли основные '
                    'проблемы. Без выдумок.'
                ),
            },
            {'role': 'user', 'content': context_text},
        ], model=model)
        summary = response.text
        llm_call = LLMCallLog.objects.create(
            provider=getattr(llm_provider, 'name', 'unknown'),
            model=response.model or 'unknown',
            purpose=LLMCallLog.Purpose.GIP_REPORT,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            latency_ms=response.latency_ms,
            status=LLMCallLog.Status.OK,
            prompt_version=GIP_REPORT_PROMPT_VERSION,
            user=user,
        )
    except Exception as exc:  # noqa: BLE001 — отчёт важнее сбоя LLM
        logger.exception('LLM для отчёта ГИПа упал: %s', exc)
        llm_call = LLMCallLog.objects.create(
            provider=getattr(llm_provider, 'name', 'unknown'),
            model=getattr(llm_provider, 'model', 'unknown'),
            purpose=LLMCallLog.Purpose.GIP_REPORT,
            status=LLMCallLog.Status.ERROR,
            error_message=str(exc)[:1000],
            prompt_version=GIP_REPORT_PROMPT_VERSION,
            user=user,
        )

    payload = dict(aggregates)
    payload['summary'] = summary
    return GeneratedReport.objects.create(
        project=project,
        period=f'{period_start.isoformat()}..{period_end.isoformat()}',
        payload=payload,
        llm_call=llm_call,
        created_by=user,
    )


def _report_period(report) -> tuple:
    start_text, end_text = report.period.split('..')
    from datetime import date as _date

    return _date.fromisoformat(start_text), _date.fromisoformat(end_text)


def _report_rows(report) -> list[list[str]]:
    """Полный список замечаний: №, документ, лист, статус, срок,
    ответственный, текст (блок 6: приложение отчёта)."""
    period_start, period_end = _report_period(report)
    rows = []
    for index, issue in enumerate(
        _report_issues(report.project, period_start, period_end), start=1,
    ):
        rows.append([
            str(index),
            issue.document.code or issue.document.name,
            str(issue.page_number) if issue.page_number else '—',
            ISSUE_STATUS_LABELS.get(issue.status, issue.status),
            issue.due_date.strftime('%d.%m.%Y') if issue.due_date else '—',
            _user_label(issue.assignee),
            issue.description,
        ])
    return rows


def _report_document_breakdown(report) -> list[dict]:
    """Разбивка замечаний по документам со статусами (блок 6).

    [{document, total, statuses}] — statuses в виде «Открыто: 2, В работе: 1».
    """
    period_start, period_end = _report_period(report)
    breakdown: dict[str, dict[str, int]] = {}
    order: list[str] = []
    for issue in _report_issues(report.project, period_start, period_end):
        code = issue.document.code or issue.document.name
        if code not in breakdown:
            breakdown[code] = {}
            order.append(code)
        status = ISSUE_STATUS_LABELS.get(issue.status, issue.status)
        breakdown[code][status] = breakdown[code].get(status, 0) + 1
    return [
        {
            'document': code,
            'total': sum(breakdown[code].values()),
            'statuses': ', '.join(
                f'{name}: {count}'
                for name, count in breakdown[code].items()
            ),
        }
        for code in order
    ]


def _report_title(report) -> str:
    period_start, period_end = _report_period(report)
    return (
        f'Отчет ГИПа по проекту {report.project.name} '
        f'({report.project.code}) за период '
        f'{period_start.strftime("%d.%m.%Y")} — '
        f'{period_end.strftime("%d.%m.%Y")}'
    )


def _report_summary_text(report) -> str:
    payload = report.payload or {}
    lines = [
        f'Всего замечаний: {payload.get("total_issues", 0)}',
        f'Просрочено: {payload.get("overdue_count", 0)}',
        'По статусам: ' + ', '.join(
            f'{ISSUE_GROUP_LABELS[group]}: '
            f'{payload.get("by_status", {}).get(group, 0)}'
            for group in ISSUE_GROUP_ORDER
        ),
        f'Резюме: {payload.get("summary", "")}',
    ]
    return '\n'.join(lines)


def _report_conclusions(report) -> list[str]:
    """Выводы для экспорта (треб. 12): короткие тезисы из агрегатов.

    Детерминированные (без LLM) — чтобы PDF/Word не зависели от сбоев
    синтеза; резюме LLM печатается отдельным блоком.
    """
    payload = report.payload or {}
    conclusions: list[str] = []
    total = payload.get('total_issues', 0)
    by_status = payload.get('by_status', {})
    if not total:
        conclusions.append(
            'За период замечания не регистрировались — внимание к '
            'документам проекта не требуется.'
        )
        return conclusions
    conclusions.append(f'За период зарегистрировано замечаний: {total}.')
    overdue = payload.get('overdue_count', 0)
    if overdue:
        conclusions.append(
            f'Просрочено {overdue} замечаний — требуется контроль сроков.'
        )
    else:
        conclusions.append('Просроченных замечаний нет.')
    progress = by_status.get('progress', 0) + by_status.get('open', 0)
    if progress:
        conclusions.append(
            f'Незавершённые замечания: {progress} '
            '(открытые и в работе).'
        )
    if payload.get('by_assignee'):
        top_name, top_count = max(
            payload['by_assignee'].items(), key=lambda kv: kv[1],
        )
        conclusions.append(
            f'Больше всего замечаний у исполнителя {top_name}: {top_count}.'
        )
    if payload.get('top_issues'):
        conclusions.append(
            'Наиболее значимые замечания перечислены в таблице выше.'
        )
    return conclusions


def _register_cyrillic_font() -> tuple[str, str]:
    """TTF-шрифт с кириллицей для экспорта отчётов (блок 3 зачистки).

    Раньше использовался CID-шрифт STSong-Light (Adobe-GB1): кириллица
    шла «разрядкой» с подстановкой глифов, шрифт не встраивался в PDF —
    это был дефект бэта-теста. Порядок поиска (строго по ТЗ):
    static/fonts/GolosText-Regular.ttf → static/fonts/DejaVuSans.ttf →
    системный DejaVuSans. Возвращает пару (regular, bold); bold может
    совпадать с regular, если найден только один файл. Если TTF с
    кириллицей нет нигде — RuntimeError с понятной ошибкой (битая
    кириллица в PDF хуже честного сбоя экспорта).
    """
    from pathlib import Path

    from django.conf import settings as dj_settings

    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    roots: list[Path] = []
    base_dir = getattr(dj_settings, 'BASE_DIR', None)
    if base_dir is not None:
        roots.append(Path(base_dir))
    # корень репозитория (ai_checks/domain/services.py → на 2 уровня вверх)
    roots.append(Path(__file__).resolve().parents[2])

    regular_candidates: list[tuple[str, Path]] = []
    bold_candidates: list[tuple[str, Path]] = []
    for root in roots:
        fonts_dir = root / 'static' / 'fonts'
        regular_candidates.extend([
            ('GolosText', fonts_dir / 'GolosText-Regular.ttf'),
            ('GolosText', fonts_dir / 'GolosText.ttf'),
            ('DejaVuSans', fonts_dir / 'DejaVuSans.ttf'),
        ])
        bold_candidates.extend([
            ('GolosText-Bold', fonts_dir / 'GolosText-Bold.ttf'),
            ('DejaVuSans-Bold', fonts_dir / 'DejaVuSans-Bold.ttf'),
        ])
    system_fonts = Path('/usr/share/fonts/truetype/dejavu')
    regular_candidates.extend([
        ('DejaVuSans', system_fonts / 'DejaVuSans.ttf'),
        ('DejaVuSans', system_fonts / 'DejaVuSansCondensed.ttf'),
    ])
    bold_candidates.append(
        ('DejaVuSans-Bold', system_fonts / 'DejaVuSans-Bold.ttf'),
    )

    for name, path in regular_candidates:
        if not path.exists():
            continue
        pdfmetrics.registerFont(TTFont(name, str(path)))
        bold_name = name
        for bold_name_candidate, bold_path in bold_candidates:
            if bold_path.exists():
                pdfmetrics.registerFont(
                    TTFont(bold_name_candidate, str(bold_path)),
                )
                bold_name = bold_name_candidate
                break
        addMapping(name, 0, 0, name)
        addMapping(name, 1, 0, bold_name)
        return name, bold_name

    # Блок 3 зачистки: заглушки STSong-Light больше нет — она давала
    # битую кириллицу («разрядка», подстановочные глифы). Честная ошибка.
    searched = '\n'.join(
        f'  - {path}' for _name, path in regular_candidates
    )
    raise RuntimeError(
        'Экспорт PDF недоступен: не найден TTF-шрифт с кириллицей. '
        'Положите GolosText-Regular.ttf или DejaVuSans.ttf в '
        'static/fonts/ (bold: GolosText-Bold.ttf / DejaVuSans-Bold.ttf) '
        'либо установите системный DejaVuSans. Проверенные пути:\n'
        f'{searched}'
    )


def gip_report_pdf(report) -> bytes:
    """PDF отчёта ГИПа (reportlab/platypus) — блоки 5–6 бэта-фиксов.

    Шрифт с кириллицей (GolosText/DejaVuSans TTF; без них — понятная
    ошибка, блок 3 зачистки).
    Структура (как у отчётов Аудитора):
    1) титульный блок (проект, период, дата, автор);
    2) краткое резюме (LLM);
    3) статистика (таблица по статусам + по ответственным);
    4) детализация по документам (таблица);
    5) ключевые проблемы (топ-10);
    6) выводы и рекомендации;
    7) приложение — полный список замечаний.
    """
    import io

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    font, font_bold = _register_cyrillic_font()
    accent = colors.HexColor('#3D5A98')
    grid = colors.HexColor('#B9C2CC')

    styles = {
        'title': ParagraphStyle(
            'title', fontName=font_bold, fontSize=15, leading=20,
            spaceAfter=8,
        ),
        'meta': ParagraphStyle(
            'meta', fontName=font, fontSize=9.5, leading=13,
            textColor=colors.HexColor('#444444'),
        ),
        'h2': ParagraphStyle(
            'h2', fontName=font_bold, fontSize=12, leading=16,
            spaceBefore=12, spaceAfter=4,
        ),
        'body': ParagraphStyle(
            'body', fontName=font, fontSize=9.5, leading=13.5,
        ),
        'cell': ParagraphStyle(
            'cell', fontName=font, fontSize=8, leading=10.5,
        ),
        'cell_head': ParagraphStyle(
            'cell_head', fontName=font_bold, fontSize=8, leading=10.5,
            textColor=colors.white,
        ),
    }

    payload = report.payload or {}
    period_start, period_end = _report_period(report)
    author = _user_label(report.created_by)

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=_report_title(report),
        author=author,
    )

    def _para(text: str, style: str) -> Paragraph:
        return Paragraph(str(text), styles[style])

    def _table(data: list, widths: list) -> Table:
        table = Table(data, colWidths=widths, repeatRows=1)
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), font),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.4, grid),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BACKGROUND', (0, 0), (-1, 0), accent),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1),
             [colors.white, colors.HexColor('#F2F5F8')]),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        return table

    def _header_cells(headers: tuple) -> list:
        return [_para(header, 'cell_head') for header in headers]

    story = []

    # 1) Титульный блок.
    story.append(_para(_report_title(report), 'title'))
    by_status = payload.get('by_status', {})
    story.append(_para(
        f'Проект: {report.project.name} · шифр {report.project.code}',
        'meta',
    ))
    story.append(_para(
        f'Период: {period_start.strftime("%d.%m.%Y")} — '
        f'{period_end.strftime("%d.%m.%Y")}',
        'meta',
    ))
    story.append(_para(
        f'Дата формирования: {timezone.localdate().strftime("%d.%m.%Y")} · '
        f'Автор: {author}',
        'meta',
    ))

    # 2) Краткое резюме (LLM; при сбое — деградация, см. generate_gip_report).
    story.append(_para('Краткое резюме', 'h2'))
    summary_text = str(payload.get('summary', '')) or '—'
    for paragraph in summary_text.split('\n'):
        if paragraph.strip():
            story.append(_para(paragraph.strip(), 'body'))

    # 3) Статистика: по статусам + по ответственным.
    story.append(_para('Статистика', 'h2'))
    stats = [[
        _para('Показатель', 'cell_head'),
        _para('Значение', 'cell_head'),
    ]]
    stats.append([_para('Всего замечаний', 'body'),
                  _para(str(payload.get('total_issues', 0)), 'body')])
    for group in ISSUE_GROUP_ORDER:
        stats.append([
            _para(ISSUE_GROUP_LABELS[group], 'body'),
            _para(str(by_status.get(group, 0)), 'body'),
        ])
    stats.append([_para('Просрочено', 'body'),
                  _para(str(payload.get('overdue_count', 0)), 'body')])
    story.append(_table(stats, [110 * mm, 30 * mm]))

    if payload.get('by_assignee'):
        story.append(Spacer(1, 6))
        assignee_table = [[
            _para('Ответственный', 'cell_head'),
            _para('Замечаний', 'cell_head'),
        ]]
        for name, count in payload['by_assignee'].items():
            assignee_table.append([
                _para(name, 'body'), _para(str(count), 'body'),
            ])
        story.append(_table(assignee_table, [110 * mm, 30 * mm]))

    # 4) Детализация по документам.
    story.append(_para('Детализация по документам', 'h2'))
    documents = _report_document_breakdown(report)
    if documents:
        document_rows = [_header_cells(
            ('Документ', 'Замечаний', 'Статусы'),
        )]
        for item in documents:
            document_rows.append([
                _para(item['document'], 'cell'),
                _para(item['total'], 'cell'),
                _para(item['statuses'], 'cell'),
            ])
        story.append(_table(document_rows, [55 * mm, 25 * mm, 60 * mm]))
    else:
        story.append(_para('Замечаний за период нет.', 'body'))

    # 5) Ключевые проблемы (топ-10 из payload).
    story.append(_para('Ключевые проблемы', 'h2'))
    top_issues = payload.get('top_issues') or []
    if top_issues:
        for index, issue in enumerate(top_issues[:10], start=1):
            parts = [f"{index}. {issue.get('message', '')}"]
            details = [issue.get('document', '')]
            if issue.get('page'):
                details.append(f"лист {issue['page']}")
            if issue.get('status'):
                details.append(issue['status'])
            if issue.get('due_date'):
                details.append(f"срок {issue['due_date']}")
            if issue.get('assignee'):
                details.append(f"ответственный: {issue['assignee']}")
            story.append(_para(
                ' '.join(parts) + ' — ' + '; '.join(filter(None, details)),
                'body',
            ))
    else:
        story.append(_para('Ключевых проблем не выделено.', 'body'))

    # 6) Выводы и рекомендации (треб. 12; детерминированные, без LLM).
    story.append(_para('Выводы и рекомендации', 'h2'))
    for conclusion in _report_conclusions(report):
        story.append(_para(f'• {conclusion}', 'body'))

    # 7) Приложение: полный список замечаний.
    rows = _report_rows(report)
    if rows:
        story.append(PageBreak())
        story.append(_para('Приложение. Полный список замечаний', 'h2'))
        appendix = [_header_cells((
            '№', 'Документ', 'Лист', 'Статус', 'Срок',
            'Ответственный', 'Замечание',
        ))]
        for row in rows:
            appendix.append([
                _para(value, 'cell') for value in row
            ])
        story.append(_table(appendix, [
            8 * mm, 32 * mm, 12 * mm, 22 * mm, 20 * mm, 30 * mm, 48 * mm,
        ]))

    def _draw_page_number(canvas, _document) -> None:
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor('#777777'))
        canvas.drawCentredString(
            A4[0] / 2, 8 * mm, str(canvas.getPageNumber()),
        )
        canvas.restoreState()

    document.build(
        story,
        onFirstPage=_draw_page_number,
        onLaterPages=_draw_page_number,
    )
    return buffer.getvalue()


def gip_report_docx(report) -> bytes:
    """Word-отчёт ГИПа (python-docx) — блок 6: структура как в PDF.

    Титульный блок → краткое резюме (LLM) → статистика → детализация
    по документам → ключевые проблемы → выводы и рекомендации →
    приложение (полный список замечаний).
    """
    import io

    from docx import Document as DocxDocument
    from docx.shared import Pt

    document = DocxDocument()
    payload = report.payload or {}
    period_start, period_end = _report_period(report)
    author = _user_label(report.created_by)
    by_status = payload.get('by_status', {})

    # Титульный блок.
    document.add_heading(_report_title(report), level=1)
    document.add_paragraph(
        f'Проект: {report.project.name} · шифр {report.project.code}',
    )
    document.add_paragraph(
        f'Период: {period_start.strftime("%d.%m.%Y")} — '
        f'{period_end.strftime("%d.%m.%Y")}',
    )
    document.add_paragraph(
        f'Дата формирования: {timezone.localdate().strftime("%d.%m.%Y")} · '
        f'Автор: {author}',
    )

    # Краткое резюме (LLM).
    document.add_heading('Резюме (LLM)', level=2)
    summary_text = str(payload.get('summary', '')) or '—'
    for paragraph in summary_text.split('\n'):
        if paragraph.strip():
            document.add_paragraph(paragraph.strip())

    # Статистика: по статусам + по ответственным.
    document.add_heading('Статистика', level=2)
    stats = document.add_table(rows=1, cols=2)
    stats.style = 'Table Grid'
    for cell, header in zip(stats.rows[0].cells, ('Показатель', 'Значение')):
        cell.text = header
    stats_rows = [('Всего замечаний', payload.get('total_issues', 0))]
    stats_rows.extend(
        (ISSUE_GROUP_LABELS[group], by_status.get(group, 0))
        for group in ISSUE_GROUP_ORDER
    )
    stats_rows.append(('Просрочено', payload.get('overdue_count', 0)))
    for name, count in stats_rows:
        cells = stats.add_row().cells
        cells[0].text = str(name)
        cells[1].text = str(count)

    if payload.get('by_assignee'):
        assignee = document.add_table(rows=1, cols=2)
        assignee.style = 'Table Grid'
        for cell, header in zip(
            assignee.rows[0].cells,
            ('Ответственный', 'Замечаний'),
        ):
            cell.text = header
        for name, count in payload['by_assignee'].items():
            cells = assignee.add_row().cells
            cells[0].text = str(name)
            cells[1].text = str(count)

    # Детализация по документам.
    document.add_heading('Детализация по документам', level=2)
    documents = _report_document_breakdown(report)
    if documents:
        document_table = document.add_table(rows=1, cols=3)
        document_table.style = 'Table Grid'
        for cell, header in zip(
            document_table.rows[0].cells,
            ('Документ', 'Замечаний', 'Статусы'),
        ):
            cell.text = header
        for item in documents:
            cells = document_table.add_row().cells
            cells[0].text = item['document']
            cells[1].text = str(item['total'])
            cells[2].text = item['statuses']
    else:
        document.add_paragraph('Замечаний за период нет.')

    # Ключевые проблемы (топ-10 из payload).
    document.add_heading('Ключевые проблемы', level=2)
    top_issues = payload.get('top_issues') or []
    if top_issues:
        for index, issue in enumerate(top_issues[:10], start=1):
            details = [issue.get('document', '')]
            if issue.get('page'):
                details.append(f"лист {issue['page']}")
            if issue.get('status'):
                details.append(issue['status'])
            if issue.get('due_date'):
                details.append(f"срок {issue['due_date']}")
            if issue.get('assignee'):
                details.append(f"ответственный: {issue['assignee']}")
            paragraph = document.add_paragraph(style='List Number')
            run = paragraph.add_run(str(issue.get('message', '')))
            run.font.size = Pt(10)
            document.add_paragraph(
                '    — ' + '; '.join(filter(None, details)),
            )
    else:
        document.add_paragraph('Ключевых проблем не выделено.')

    # Выводы и рекомендации (треб. 12).
    document.add_heading('Выводы и рекомендации', level=2)
    for conclusion in _report_conclusions(report):
        document.add_paragraph(conclusion, style='List Bullet')

    # Приложение: полный список замечаний.
    rows = _report_rows(report)
    if rows:
        document.add_heading('Приложение. Полный список замечаний', level=2)
        table = document.add_table(rows=1, cols=7)
        table.style = 'Table Grid'
        for cell, header in zip(
            table.rows[0].cells,
            ('№', 'Документ', 'Лист', 'Статус', 'Срок',
             'Ответственный', 'Замечание'),
        ):
            cell.text = header
        for row in rows:
            cells = table.add_row().cells
            for cell, value in zip(cells, row):
                cell.text = str(value)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


import base64


def bbox_ratios_for_rotation(
    file_path: str,
    page_number: int | None,
    bbox,
    viewer_rotation: int | None = None,
) -> dict:
    """Ratios bbox с учётом поворота листа (vьювер/PDF).

    Координаты bbox — неповёрнутая система (как в PyMuPDF); вьювер рисует
    лист с поворотом (viewer_rotation — текущий поворот вьювера; если не
    задан, берётся page.rotation из PDF). Возвращает x1/y1/x2/y2/x/y
    в долях 0..1 или {} (если данных нет).

    Неоткрываемый файл (PdfiumError: обрыв при передаче, пустой файл)
    тоже даёт {} — подтверждение находки не должно падать 500
    (view подставляет ratios по умолчанию, кроп не строится).
    """
    if bbox is None or page_number is None:
        return {}
    try:
        with pdfium_tools.open_pdf(file_path) as pdf:
            if not (1 <= int(page_number) <= len(pdf)):
                return {}
            view = pdfium_tools.PdfPageView(pdf, int(page_number) - 1)
            rotation = (
                int(viewer_rotation)
                if viewer_rotation is not None and str(viewer_rotation) != ''
                else int(view.rotation or 0)
            ) % 360

            width = float(view.mediabox.width)
            height = float(view.mediabox.height)
            view.close()
            x0, y0, x1, y1 = (float(v) for v in bbox)
            x0, x1 = min(x0, x1), max(x0, x1)
            y0, y1 = min(y0, y1), max(y0, y1)

            if rotation == 90:
                px0, py0 = height - y1, x0
                px1, py1 = height - y0, x1
                display_w, display_h = height, width
            elif rotation == 180:
                px0, py0 = width - x1, height - y1
                px1, py1 = width - x0, height - y0
                display_w, display_h = width, height
            elif rotation == 270:
                px0, py0 = y0, width - x1
                px1, py1 = y1, width - x0
                display_w, display_h = height, width
            else:
                px0, py0, px1, py1 = x0, y0, x1, y1
                display_w, display_h = width, height
    except pdfium_tools.PdfiumError:
        return {}
    except (ValueError, TypeError) as exc:
        # Бэта-тест 2026-11-08: viewer_rotation приходит из hx-vals кнопки —
        # подделанный/повреждённый запрос (rotation='abc') или битый bbox в
        # БД давали ValueError -> 500 «Подтвердить». Пустой результат —
        # вьюха подставит координаты по умолчанию, кроп не строится.
        logger.warning(
            'bbox_ratios_for_rotation: некорректные данные (%s: %s)',
            type(exc).__name__, exc,
        )
        return {}

    def ratio(value: float, size: float) -> float:
        return min(1.0, max(0.0, value / size))

    x1_ratio = ratio(px0, display_w)
    y1_ratio = ratio(py0, display_h)
    x2_ratio = ratio(px1, display_w)
    y2_ratio = ratio(py1, display_h)
    if x1_ratio >= x2_ratio or y1_ratio >= y2_ratio:
        return {}
    return {
        'x1_ratio': x1_ratio,
        'y1_ratio': y1_ratio,
        'x2_ratio': x2_ratio,
        'y2_ratio': y2_ratio,
        'x_ratio': (x1_ratio + x2_ratio) / 2.0,
        'y_ratio': (y1_ratio + y2_ratio) / 2.0,
    }


def crop_finding_image(
    file_path: str,
    page_number: int,
    bbox,
    margin: float = 8.0,
    zoom: float = 2.0,
) -> str | None:
    """Кроп bbox из PDF (JPEG, base64) для Issue.crop_image.

    create_issue ожидает base64 JPEG (магия \\xff\\xd8, ≤5 МБ).
    """
    if bbox is None or page_number is None:
        return None
    with pdfium_tools.open_pdf(file_path) as pdf:
        if not (1 <= int(page_number) <= len(pdf)):
            return None
        view = pdfium_tools.PdfPageView(pdf, int(page_number) - 1)
        page_rect = view.mediabox
        clip = pdfium_tools.Rect(
            max(page_rect.x0, float(bbox[0]) - margin),
            max(page_rect.y0, float(bbox[1]) - margin),
            min(page_rect.x1, float(bbox[2]) + margin),
            min(page_rect.y1, float(bbox[3]) + margin),
        )
        if clip.is_empty or clip.width < 1 or clip.height < 1:
            view.close()
            return None
        try:
            data = view.render_jpeg_clip(clip, scale=zoom)
            # Бэта-тест 2026-11-08: create_issue отклоняет кроп > 5 МБ
            # (ValueError -> был 500 в confirm_finding). Плотный
            # крупноформатный лист при zoom=2 легко превышает лимит —
            # пересобираем кроп с меньшим масштабом/качеством (до 3 раз).
            quality = 95
            scale = zoom
            for _ in range(3):
                if len(data) <= 4 * 1024 * 1024:
                    break
                scale = max(0.5, scale * 0.6)
                quality = max(60, quality - 20)
                data = view.render_jpeg_clip(
                    clip, scale=scale, quality=quality,
                )
        finally:
            view.close()
    return base64.b64encode(data).decode('ascii')


# ---------- База знаний (UX-аудит stage 2) ----------

KNOWLEDGE_DOC_TYPES = ('customer_req', 'internal_standard', 'past_issues')


def upload_knowledge_document(
    user,
    project,
    uploaded_file,
    title: str,
    doc_type: str,
    tenant_scope: str,
) -> InternalDocument:
    """Загрузка документа в базу знаний (файл — через storage/FileField).

    Создаёт storage.Document + версию сервисом upload_document (единый
    FileStorage-путь проекта), затем InternalDocument с tenant_scope.
    Индексация запускается отдельно (Celery-задача).
    """
    from uuid import uuid4

    from storage.domain.models import Section
    from storage.domain.services import upload_document

    if doc_type not in KNOWLEDGE_DOC_TYPES:
        raise ValueError('Неизвестный тип документа базы знаний')
    if tenant_scope not in ('project', 'company'):
        raise ValueError('Область видимости: только проект или компания')

    section = (
        Section.objects.filter(project=project).order_by('order', 'pk').first()
    )
    if section is None:
        raise ValueError(
            'В проекте нет разделов — нельзя сохранить файл базы знаний',
        )

    version = upload_document(
        user=user,
        project=project,
        section=section,
        code=f'KB-{uuid4().hex[:10].upper()}',
        name=title or uploaded_file.name,
        doc_type='report',
        file=uploaded_file,
        # База знаний индексируется в InternalChunk, а не в проектный
        # индекс ProjectDocumentChunk (треб. 6: тип источника
        # «проект» = PDF-документы проекта, «стандарт» = база знаний).
        skip_ai_index=True,
    )
    document = version.document if hasattr(version, 'document') else version

    return InternalDocument.objects.create(
        tenant_scope=tenant_scope,
        company=project.company if tenant_scope == 'company' else None,
        project=project if tenant_scope == 'project' else None,
        source_document=document,
        doc_type=doc_type,
        title=title or uploaded_file.name,
        created_by=user,
        indexing_status=IndexingStatus.PENDING,
    )


def index_internal_document(internal_document_id: int) -> int:
    """Индексация InternalDocument со статусами (pending→processing→done/error)."""
    try:
        internal = InternalDocument.objects.select_related(
            'source_document',
        ).get(pk=internal_document_id)
    except InternalDocument.DoesNotExist:
        return 0

    internal.indexing_status = IndexingStatus.PROCESSING
    internal.save(update_fields=['indexing_status'])
    try:
        version = internal.source_document.current_version
        if version is None:
            raise ValueError('У исходного документа нет текущей версии')
        count = index_document(internal, version.file.path)
        internal.indexing_status = IndexingStatus.DONE
        internal.index_error = ''
        internal.save(update_fields=['indexing_status', 'index_error'])
        return count
    except Exception as exc:  # noqa: BLE001 — статус важнее трейса
        logger.exception('Индексация базы знаний #%s упала: %s', internal.pk, exc)
        internal.indexing_status = IndexingStatus.ERROR
        internal.index_error = str(exc)[:1000]
        internal.save(update_fields=['indexing_status', 'index_error'])
        return 0


def remove_knowledge_document(internal_document) -> None:
    """Мягкое удаление из базы знаний (is_active=False)."""
    internal_document.is_active = False
    internal_document.save(update_fields=['is_active'])


def knowledge_documents(project):
    """Документы базы знаний проекта: проектные + компанейские (активные)."""
    from django.db.models import Count, Q

    return (
        InternalDocument.objects
        .filter(is_active=True)
        .filter(
            Q(tenant_scope='project', project=project)
            | Q(tenant_scope='company', company=project.company)
        )
        .select_related('source_document')
        .annotate(chunk_count=Count('chunks'))
        .order_by('-created_at', '-pk')
    )
