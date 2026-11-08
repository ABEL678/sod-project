"""PgVectorBackend — векторное хранилище PostgreSQL + pgvector (Этап 19).

Единственная реализация VectorBackend (ARCHITECTURE.md 5.5): HNSW/cosine.
Ф0.2: реальный поиск (оператор `<=>` через ORM, CosineDistance) с
tenant-фильтрами по правилу 8.2. На SQLite (dev-альтернатива) поиск
недоступен — единственный кодовый путь dev = prod через Docker/PG (8.3).
"""

from __future__ import annotations

from typing import Any

from django.db import connection
from django.db.models import Q
from pgvector.django import CosineDistance

from ai_checks.domain.models import InternalChunk, ProjectDocumentChunk
from ai_checks.ports.vector import VectorBackend


def visibility_filter(filters: dict | None) -> Q:
    """Правило выборки tenant_scope (ARCHITECTURE.md 8.2).

    Видимы: global-источники всем; company-источники — только своей
    компании; project-источники — только своему проекту. Без tenant-
    контекста видимы только global (fail-closed).
    """
    filters = filters or {}
    company_id = filters.get('company_id')
    project_id = filters.get('project_id')

    visibility = Q(document__tenant_scope='global')
    if company_id:
        visibility |= Q(
            document__tenant_scope='company',
            document__company_id=company_id,
        )
    if project_id:
        visibility |= Q(
            document__tenant_scope='project',
            document__project_id=project_id,
        )
    return visibility


class PgVectorBackend(VectorBackend):
    """PostgreSQL + pgvector: HNSW/cosine-поиск по InternalChunk."""

    def __init__(self, dimension: int = 1024) -> None:
        self.dimension = dimension

    def upsert(self, chunks: Any) -> None:
        """Записывает/обновляет векторы фрагментов (InternalChunk)."""
        raise NotImplementedError(
            'PgVectorBackend.upsert будет реализован в следующем шаге Ф0.2 '
            '(пакетная запись чанков через VectorBackend).'
        )

    def search(
        self,
        vector: Any,
        filters: Any,
        top_k: int,
    ) -> list:
        """Поиск ближайших соседей (cosine) с tenant-фильтрами.

        Возвращает список hits: id, text, document_id, score (cosine-
        similarity = 1 - distance), page_from, page_to.
        """
        if connection.vendor != 'postgresql':
            raise NotImplementedError(
                'PgVectorBackend.search доступен только на PostgreSQL'
            )

        filters = filters or {}
        top_k = max(1, int(top_k or 1))

        queryset = InternalChunk.objects.filter(
            document__is_active=True,
        ).filter(visibility_filter(filters))

        scope = filters.get('tenant_scope')
        if scope:
            queryset = queryset.filter(document__tenant_scope=scope)

        rows = (
            queryset
            .annotate(distance=CosineDistance('embedding', vector))
            .order_by('distance')[:top_k]
        )
        return [
            {
                'id': chunk.id,
                'text': chunk.text,
                'document_id': chunk.document_id,
                'score': float(1.0 - chunk.distance),
                'page_from': chunk.page_from,
                'page_to': chunk.page_to,
            }
            for chunk in rows
        ]


class ProjectChunkVectorBackend:
    """Cosine-поиск по фрагментам PDF-документов проекта (треб. 6).

    Контракт тот же, что у VectorBackend.search(vector, filters, top_k).
    Фильтры: project_id (обязателен), section_code (опционально — раздел
    ОВ/ЭОМ/АР). Ищутся чанки неудалённых документов проекта; чанки
    KB-* документов здесь не встречаются (они живут в InternalChunk).
    На SQLite — NotImplementedError (как у PgVectorBackend.search).
    """

    def __init__(self, dimension: int = 1024) -> None:
        self.dimension = dimension

    def search(
        self,
        vector: Any,
        filters: Any,
        top_k: int,
    ) -> list:
        if connection.vendor != 'postgresql':
            raise NotImplementedError(
                'Поиск по документам проекта доступен только на PostgreSQL'
            )

        filters = filters or {}
        project_id = filters.get('project_id')
        if not project_id:
            return []
        top_k = max(1, int(top_k or 1))

        queryset = ProjectDocumentChunk.objects.filter(
            document__project_id=project_id,
            document__is_deleted=False,
        )
        section_code = filters.get('section_code')
        if section_code:
            queryset = queryset.filter(section__code=section_code)

        rows = (
            queryset
            .annotate(distance=CosineDistance('embedding', vector))
            .order_by('distance')[:top_k]
        )
        return [
            {
                'id': chunk.id,
                'document_id': chunk.document_id,
                'section_code': (
                    chunk.section.code if chunk.section_id else None
                ),
                'score': float(1.0 - chunk.distance),
                'page_from': chunk.page_from,
                'page_to': chunk.page_to,
            }
            for chunk in rows
        ]
