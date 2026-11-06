"""Порты ИИ-контура (Этап 19): абстракции внешних сервисов.

Бизнес-логика приложения зависит только от этих Protocol-интерфейсов;
реализации живут в ``ai_checks/adapters/``. См. ARCHITECTURE.md 5.3–5.5.
"""

from ai_checks.ports.embedding import EmbeddingService
from ai_checks.ports.llm import LLMProvider, LLMResponse
from ai_checks.ports.vector import VectorBackend

__all__ = [
    'EmbeddingService',
    'LLMProvider',
    'LLMResponse',
    'VectorBackend',
]
