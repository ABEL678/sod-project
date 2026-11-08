"""Живой дедлайн-бейдж и статус-бейдж для карточки замечания.

Возвращает человеческий ярлык («осталось 5 дней», «просрочено на 2 дня»)
и CSS-класс из дизайн-токенов (cde-deadline / --soon / --overdue).
Использование:
    {% load issue_extras %}
    {{ issue|deadline_label }}
    {{ issue|deadline_class }}
    <span class="{{ issue|status_badge_class }}">{{ issue.get_status_display }}</span>
"""
from datetime import date

from django import template

from storage.domain.design_colors import DEFAULT_STATUS, ISSUE_STATUS_CLASS
from storage.domain.services import ru_plural

register = template.Library()


@register.filter
def plural_ru(number, forms):
    """Русская плюрализация тремя формами: «е,я,й» ->
    замечание / замечания / замечаний. Джанговский pluralize трёх
    форм не поддерживает (возвращает '' при 3 аргументах)."""
    one, few, many = (part.strip() for part in forms.split(',', 2))
    return ru_plural(number, one, few, many)

# Живой срок показываем только по активным замечаниям:
# у закрытых и аннулированных дедлайн уже не давит.
ACTIVE_STATUSES = {"NEW", "IN_PROGRESS", "DISCUSSION", "REJECTED"}


def _days_left(issue):
    due = getattr(issue, "due_date", None)
    if not due or getattr(issue, "status", "") not in ACTIVE_STATUSES:
        return None
    return (due - date.today()).days


def _plural_days(number):
    number = abs(number)
    if number % 10 == 1 and number % 100 != 11:
        return "день"
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return "дня"
    return "дней"


@register.filter
def deadline_label(issue):
    """Текст живого бейджа срока ответа."""
    days = _days_left(issue)
    if days is None:
        return ""
    if days > 0:
        return "осталось {} {}".format(days, _plural_days(days))
    if days == 0:
        return "срок сегодня"
    return "просрочено на {} {}".format(abs(days), _plural_days(days))


@register.filter
def deadline_class(issue):
    """CSS-классы живого бейджа (см. .cde-deadline в tokens.css)."""
    days = _days_left(issue)
    if days is None:
        return ""
    if days < 0:
        return "cde-deadline cde-deadline--overdue"
    if days <= 3:
        return "cde-deadline cde-deadline--soon"
    return "cde-deadline"


@register.filter
def status_badge_class(issue):
    """CSS-класс статусного бейджа из дизайн-токенов (единый источник:
    tokens.json -> design_colors.py), чтобы реестр, вьювер и экспорт
    красили статусы одинаково."""
    status = getattr(issue, "status", "")
    return ISSUE_STATUS_CLASS.get(status) or ISSUE_STATUS_CLASS[DEFAULT_STATUS]
