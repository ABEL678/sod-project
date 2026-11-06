"""Порт LLM-провайдера (ARCHITECTURE.md 5.3, Этап 19).

Реализация MVP: ``OpenRouterHTTPProvider`` (httpx, без SDK; retry ×3,
таймаут 60 с) — появится в следующей фазе. Здесь только контракт.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class LLMResponse:
    """Ответ модели: текст и метаданные для LLMCallLog.

    Тексты промптов и ответов в БД не хранятся (приватность компаний,
    ARCHITECTURE.md 3.2) — здесь только то, что нужно вызывающему коду.
    """

    text: str
    model: str = ''
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    """Контракт провайдера LLM."""

    def complete(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Возвращает ответ модели на список сообщений (role/content)."""
        ...
