"""Домен ИИ-контура: бизнес-логика Ф0.2 — индексация источников знаний.

Контракт (ARCHITECTURE.md 8.4): PyMuPDF извлекает текст и страницы,
текст режется на чанки с перекрытием, векторы пишутся в InternalChunk.
Сервис не знает про HTTP/UI/Celery — только домен и ORM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pymupdf
from django.db import transaction
from django.utils import timezone

from ai_checks.domain.models import (
    CheckRun,
    InternalChunk,
    InternalDocument,
    PendingCheckResult,
    RuleDefinition,
)
from ai_checks.domain.rules import RULE_CHECKS
from ai_checks.ports.embedding import EmbeddingService
from storage.domain.models import DocumentVersion

logger = logging.getLogger(__name__)

# Версия набора правил/промптов прогона (CheckRun.engine_version).
ENGINE_VERSION = 'rules@2026.11.1'

# Размер чанка и перекрытие (ТЗ Ф0.2: ~1000 символов, перекрытие 200).
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200


@dataclass
class TextChunk:
    """Фрагмент текста с границами страниц."""

    text: str
    page_from: int | None
    page_to: int | None


def extract_pages(file_path: str) -> list[str]:
    """Извлекает текст постранично из PDF (PyMuPDF)."""
    with pymupdf.open(file_path) as pdf:
        return [page.get_text() for page in pdf]


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
        # Ленивый импорт: домен не зависит от конкретного адаптера.
        from ai_checks.adapters.embedding_onnx import BgeM3OnnxEmbedding

        embedding_service = BgeM3OnnxEmbedding()

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


def run_rules_check(
    document_version: DocumentVersion,
    initiated_by,
) -> CheckRun:
    """Прогон детерминированных правил по PDF версии (Ф1, без LLM).

    Создаёт CheckRun (queued → running → done/failed), прогоняет активные
    RuleDefinition на каждой странице через RULE_CHECKS и сохраняет
    PendingCheckResult (status=pending) для каждой находки.
    Записи находок и финальный статус — в transaction.atomic(); при сбое
    (битый PDF и т.п.) прогон помечается failed с текстом ошибки в meta.
    """
    check_run = CheckRun.objects.create(
        document_version=document_version,
        initiated_by=initiated_by,
        status=CheckRun.Status.QUEUED,
        engine_version=ENGINE_VERSION,
        started_at=timezone.now(),
    )
    try:
        file_path = document_version.file.path
        rules = list(
            RuleDefinition.objects.filter(is_active=True).order_by('code')
        )
        executable = [rule for rule in rules if rule.code in RULE_CHECKS]

        findings: list[tuple[RuleDefinition, dict]] = []
        with transaction.atomic():
            with pymupdf.open(file_path) as pdf:
                for page in pdf:
                    for rule in executable:
                        checker = RULE_CHECKS[rule.code]
                        for finding in checker(page):
                            findings.append((rule, finding))

            PendingCheckResult.objects.bulk_create([
                PendingCheckResult(
                    check_run=check_run,
                    rule=rule,
                    status=PendingCheckResult.Status.PENDING,
                    severity=rule.severity,
                    page=finding['page'],
                    bbox=finding['bbox'],
                    message=finding['message'],
                    evidence=finding['evidence'],
                )
                for rule, finding in findings
            ])
            check_run.status = CheckRun.Status.DONE
            check_run.rules_total = len(executable)
            check_run.findings_total = len(findings)
            check_run.finished_at = timezone.now()
            check_run.save(update_fields=[
                'status', 'rules_total', 'findings_total', 'finished_at',
            ])
    except Exception as exc:  # noqa: BLE001 — статус прогона важнее трейса
        logger.exception(
            'AI-проверка версии %s упала: %s', document_version.pk, exc,
        )
        check_run.status = CheckRun.Status.FAILED
        check_run.findings_total = 0
        check_run.meta = {'error': str(exc)}
        check_run.finished_at = timezone.now()
        check_run.save(update_fields=[
            'status', 'findings_total', 'meta', 'finished_at',
        ])
    return check_run


def rag_answer(
    question: str,
    company_id: int,
    project_id: int | None = None,
    top_k: int = 5,
    embedding_service: EmbeddingService | None = None,
    vector_backend=None,
) -> dict:
    """RAG-ответ по внутренней базе знаний (Ф2, без LLM).

    Вопрос → эмбеддинг → PgVectorBackend.search с tenant-фильтрами (8.2) →
    контекст из чанков. Тексты ответов/промптов не логируются (приватность).
    Возвращает {answer, sources}; при отсутствии чанков — «Ничего не найдено».
    answer — заглушка-дайджест (LLM-синтез — следующая фаза).
    """
    question = (question or '').strip()
    if not question:
        raise ValueError('Пустой вопрос')

    if embedding_service is None:
        # Ленивый импорт: домен не зависит от конкретного адаптера.
        from ai_checks.adapters.embedding_onnx import BgeM3OnnxEmbedding

        embedding_service = BgeM3OnnxEmbedding()
    if vector_backend is None:
        from ai_checks.adapters.vector_pg import PgVectorBackend

        vector_backend = PgVectorBackend(dimension=embedding_service.dim)

    vectors = embedding_service.embed_texts([question])
    if not vectors:
        raise ValueError('EmbeddingService не вернул вектор вопроса')

    filters = {
        'company_id': company_id,
        'project_id': project_id,
    }
    hits = vector_backend.search(vectors[0], filters, top_k)
    if not hits:
        return {'answer': 'Ничего не найдено', 'sources': []}

    chunk_by_id = {
        chunk.pk: chunk
        for chunk in InternalChunk.objects
        .select_related('document')
        .filter(pk__in=[hit['id'] for hit in hits])
    }

    sources = []
    for hit in hits:
        chunk = chunk_by_id.get(hit['id'])
        if chunk is None:
            continue
        sources.append({
            'chunk_id': chunk.pk,
            'document_title': chunk.document.title,
            'text': chunk.text,
            'score': hit.get('score'),
            'tenant_scope': chunk.document.tenant_scope,
        })
    if not sources:
        return {'answer': 'Ничего не найдено', 'sources': []}

    digest = '\n\n'.join(
        f'[{source["document_title"]}] {source["text"][:300]}'
        for source in sources[:3]
    )
    return {'answer': digest, 'sources': sources}
