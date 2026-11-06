"""Домен ИИ-контура: бизнес-логика Ф0.2 — индексация источников знаний.

Контракт (ARCHITECTURE.md 8.4): PyMuPDF извлекает текст и страницы,
текст режется на чанки с перекрытием, векторы пишутся в InternalChunk.
Сервис не знает про HTTP/UI/Celery — только домен и ORM.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf
from django.db import transaction
from django.utils import timezone

from ai_checks.domain.models import InternalChunk, InternalDocument
from ai_checks.ports.embedding import EmbeddingService

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
