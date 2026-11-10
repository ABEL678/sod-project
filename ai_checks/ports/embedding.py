"""Порт сервиса эмбеддингов (ARCHITECTURE.md 5.4, Этап 19).

Реализация по умолчанию (зачистка 2026-11-10): ``GigaChatEmbedding``
(облако Сбера, OpenAI-совместимый /v1/embeddings) — фабрика
``ai_checks.adapters.embedding_gigachat.get_embedding_service``;
офлайн-фолбэк — ``BgeM3OnnxEmbedding`` (ONNX int8, CPU, локально,
AI_EMBEDDING_PROVIDER=onnx).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingService(Protocol):
    """Контракт сервиса эмбеддингов."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Возвращает вектор для каждого текста (порядок сохраняется)."""
        ...

    @property
    def dim(self) -> int:
        """Размерность вектора: 1024 (bge-m3 dense)."""
        ...

    @property
    def model_version(self) -> str:
        """Идентификатор версии модели для InternalDocument.embedding_model."""
        ...
