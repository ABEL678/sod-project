"""Контекст-процессор: флаг ИИ-контура для шаблонов."""

from django.conf import settings


def ai_checks(request):
    return {
        'ai_checks_enabled': bool(
            getattr(settings, 'AI_CHECKS_ENABLED', False)
        ),
    }
