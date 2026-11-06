"""Ре-экспорт доменных моделей ИИ-контура (паттерн storage)."""

from ai_checks.domain.models import CheckRun, LLMCallLog

__all__ = ['CheckRun', 'LLMCallLog']
