"""Моки портов ИИ-контура для тестов (Этап 19, Ф0.1).

Без сети, без БД, без реальных моделей: детерминированные ответы,
запись вызовов для проверок. Реальные адаптеры — в следующих фазах.
"""

from __future__ import annotations

import hashlib

from ai_checks.ports.llm import LLMResponse


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
