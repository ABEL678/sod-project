"""Контекст-процессор: флаги ИИ-контура для шаблонов."""

from django.conf import settings


def _report_allowed(user) -> bool:
    """Отчёт ГИПу: только ГИП/админ (или superuser)."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, 'userprofile', None)
    return profile is not None and profile.role in ('gip', 'admin')


def ai_checks(request):
    enabled = bool(getattr(settings, 'AI_CHECKS_ENABLED', False))
    return {
        'ai_checks_enabled': enabled,
        'ai_report_allowed': enabled and _report_allowed(request.user),
    }
