"""Моки портов ИИ-контура для тестов (Этап 19, Ф0.1).

Без сети, без БД, без реальных моделей: детерминированные ответы,
запись вызовов для проверок. Реальные адаптеры — в следующих фазах.
"""

from __future__ import annotations

import hashlib
from unittest import mock

from ai_checks.ports.llm import LLMResponse


def mock_bge_adapter(testcase, dim: int = 1024) -> None:
    """Подменяет все эмбеддинги (Cloud.ru, GigaChat, ONNX) на MockEmbeddingService.

    Патчим единую точку входа — фабрику get_embedding_service().
    Это работает для всех провайдеров и для Celery-задач
    (CELERY_TASK_ALWAYS_EAGER=True), которые вызывают index_document
    в отдельном контексте.
    Вызывать из setUp: mock_bge_adapter(self).
    """
    service = MockEmbeddingService(dim=dim)
    patcher = mock.patch(
        'ai_checks.adapters.embedding_factory.get_embedding_service',
        return_value=service,
    )
    patcher.start()
    testcase.addCleanup(patcher.stop)


class MockLLMProvider:
    """Мок LLMProvider: отдаёт заранее заданные ответы по очереди.

    Если ответы не заданы — возвращает эхо-ответ с последним сообщением.
    Все вызовы записываются в ``self.calls`` (messages/model/json_mode).
    """

    def __init__(
        self,
        responses: list[LLMResponse] | None = None,
        model: str = 'mock-model',
        latency_ms: int = 5,
    ) -> None:
        self.responses = list(responses or [])
        self.model = model
        self.latency_ms = latency_ms
        self.calls: list[dict] = []

    def complete(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        self.calls.append({
            'messages': messages,
            'model': model,
            'json_mode': json_mode,
        })
        if self.responses:
            return self.responses.pop(0)
        last = messages[-1].get('content', '') if messages else ''
        return LLMResponse(
            text=f'[mock] {last}',
            model=model or self.model,
            prompt_tokens=sum(len(str(m.get('content', ''))) for m in messages),
            completion_tokens=len(last),
            latency_ms=self.latency_ms,
        )


class MockEmbeddingService:
    """Мок EmbeddingService: детерминированные псевдо-векторы из хэша.

    Размерность настраивается (по умолчанию 1024 — как bge-m3 dense);
    одинаковый текст всегда даёт одинаковый вектор.
    """

    def __init__(
        self,
        dim: int = 1024,
        model_version: str = 'mock-embedding@1',
    ) -> None:
        self._dim = dim
        self._model_version = model_version
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_version(self) -> str:
        return self._model_version

    def _vector(self, text: str) -> list[float]:
        """Детерминированный вектор из SHA-256 (значения в [-1, 1])."""
        seed = hashlib.sha256(text.encode('utf-8')).digest()
        values = []
        for index in range(self._dim):
            byte = seed[index % len(seed)]
            values.append(round((byte / 127.5) - 1.0, 6))
        return values


class MockVectorBackend:
    """Двойник VectorBackend для тестов на SQLite (без pgvector).

    Повторяет контракт PgVectorBackend.search: применяет то же правило
    видимости 8.2 (visibility_filter) и считает косинус на Python.
    Используется только в тестах; в проде — PgVectorBackend.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def search(self, vector, filters, top_k: int) -> list:
        from ai_checks.adapters.vector_pg import visibility_filter
        from ai_checks.domain.models import InternalChunk

        self.calls.append({'filters': dict(filters or {}), 'top_k': top_k})
        queryset = InternalChunk.objects.filter(
            document__is_active=True,
        ).filter(visibility_filter(filters))
        scope = (filters or {}).get('tenant_scope')
        if scope:
            queryset = queryset.filter(document__tenant_scope=scope)

        scored = []
        for chunk in queryset.select_related('document'):
            scored.append({
                'id': chunk.pk,
                'text': chunk.text,
                'document_id': chunk.document_id,
                'score': self._cosine(vector, chunk.embedding),
                'page_from': chunk.page_from,
                'page_to': chunk.page_to,
            })
        scored.sort(key=lambda hit: hit['score'], reverse=True)
        return scored[:max(1, int(top_k or 1))]

    @staticmethod
    def _cosine(left, right) -> float:
        import math

        left = [float(value) for value in left]
        right = [float(value) for value in right]
        if len(left) != len(right) or not left:
            return 0.0
        dot = sum(a * b for a, b in zip(left, right))
        norm_left = math.sqrt(sum(a * a for a in left))
        norm_right = math.sqrt(sum(b * b for b in right))
        if norm_left == 0 or norm_right == 0:
            return 0.0
        return dot / (norm_left * norm_right)


class MockProjectVectorBackend:
    """Поиск по ProjectDocumentChunk для тестов на SQLite (без pgvector).

    Повторяет контракт ProjectChunkVectorBackend.search: косинус по
    векторам чанков проекта, опциональный фильтр по разделу. Только
    неудалённые документы проекта.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def search(self, vector, filters, top_k: int) -> list:
        import math

        from ai_checks.domain.models import ProjectDocumentChunk

        filters = filters or {}
        self.calls.append({'filters': dict(filters), 'top_k': top_k})

        queryset = ProjectDocumentChunk.objects.filter(
            document__project_id=filters.get('project_id'),
            document__is_deleted=False,
        )
        if filters.get('section_code'):
            queryset = queryset.filter(
                section__code=filters['section_code'],
            )

        scored = []
        for chunk in queryset.select_related('document', 'section'):
            scored.append({
                'id': chunk.pk,
                'document_id': chunk.document_id,
                'section_code': (
                    chunk.section.code if chunk.section_id else None
                ),
                'score': self._cosine(vector, chunk.embedding),
                'page_from': chunk.page_from,
                'page_to': chunk.page_to,
            })
        scored.sort(key=lambda hit: hit['score'], reverse=True)
        return scored[:max(1, int(top_k or 1))]

    @staticmethod
    def _cosine(left, right) -> float:
        import math

        left = [float(value) for value in left]
        right = [float(value) for value in right]
        if len(left) != len(right) or not left:
            return 0.0
        dot = sum(a * b for a, b in zip(left, right))
        norm_left = math.sqrt(sum(a * a for a in left))
        norm_right = math.sqrt(sum(b * b for b in right))
        if norm_left == 0 or norm_right == 0:
            return 0.0
        return dot / (norm_left * norm_right)
