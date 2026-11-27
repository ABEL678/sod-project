"""Контекст-процессор уведомлений (Волна 3).

Добавляет в каждый шаблон счётчики колокольчика топбара:
``unread_notifications_count`` и ``my_open_issues_count``.
Запросы дешёвые (3 COUNT-запроса на страницу только для авторизованных:
непрочитанные уведомления, назначенные и созданные замечания).
"""

from django.db.models import Q

from .models import Notification
from .services import ACTIVE_ISSUE_STATUSES


def notifications_badges(request):
    if not request.user.is_authenticated:
        return {
            'unread_notifications_count': 0,
            'my_open_issues_count': 0,
        }
    # django-tables2 рендерит таблицу отдельным RequestContext и повторно
    # вызывает контекст-процессоры. На странице реестра это давало второй
    # комплект из трёх COUNT-запросов. Результат живёт ровно один запрос.
    cached = getattr(request, '_notifications_badges_cache', None)
    if cached is not None:
        return cached
    unread = Notification.objects.filter(
        recipient=request.user, is_read=False,
    ).count()
    my_open = request.user.assigned_issues.filter(
        status__in=ACTIVE_ISSUE_STATUSES,
        document__is_deleted=False,
    ).count() + request.user.created_issues.filter(
        status__in=ACTIVE_ISSUE_STATUSES,
        document__is_deleted=False,
    ).exclude(author_id=request.user.pk).count()
    payload = {
        'unread_notifications_count': unread,
        'my_open_issues_count': my_open,
    }
    request._notifications_badges_cache = payload
    return payload
