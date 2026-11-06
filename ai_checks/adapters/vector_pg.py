"""PgVectorBackend — векторное хранилище PostgreSQL + pgvector (Этап 19).

Единственная реализация VectorBackend (ARCHITECTURE.md 5.5): HNSW/cosine.
Ф0.1 — заглушка с правильными сигнатурами; реальные upsert/search и
миграция CREATE EXTENSION vector — в фазе Ф0.2 (InternalChunk.embedding).
"""

from __future__ import annotations

from typing import Any

from ai_checks.ports.vector import VectorBackend


class PgVectorBackend(VectorBackend):
    """Заглушка: контракт зафиксирован, реализация — Ф0.2."""

    def __init__(self, dimension: int = 1024) -> None:
        self.dimension = dimension

    def upsert(self, chunks: Any) -> None:
        """Записывает/обновляет векторы фрагментов (InternalChunk)."""
        raise NotImplementedError(
            'PgVectorBackend.upsert будет реализован в фазе Ф0.2 '
            '(InternalChunk + CREATE EXTENSION vector).'
        )

    def search(self, vector: Any, filters: Any, top_k: int) -> list:
        """Поиск ближайших соседей (HNSW/cosine) с tenant-фильтрами."""
        raise NotImplementedError(
            'PgVectorBackend.search будет реализован в фазе Ф0.2.'
        )
