"""Порт векторного хранилища (ARCHITECTURE.md 5.5, Этап 19).

Единственная реализация — ``PgVectorBackend`` (PostgreSQL + pgvector,
HNSW/cosine). Стабы NullVectorBackend и sqlite-vec не реализуются
(решение 06.11.2026: Docker в локальной среде подтверждён).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class VectorBackend(Protocol):
    """Контракт векторного хранилища."""

    def upsert(self, chunks: Any) -> None:
        """Записывает/обновляет векторы фрагментов (InternalChunk)."""
        ...

    def search(
        self,
        vector: Any,
        filters: Any,
        top_k: int,
    ) -> list:
        """Поиск ближайших соседей с фильтрами tenant_scope/company/project."""
        ...
