"""Контекст-процессор уведомлений (Волна 3).

Добавляет в каждый шаблон счётчики колокольчика топбара:
``unread_notifications_count`` и ``my_open_issues_count``.
Запросы дешёвые (2 COUNT-запроса на страницу только для авторизованных).
"""

from django.db.models import Q

from .models import Notification

ACTIVE_STATUSES = ('NEW', 'IN_PROGRESS', 'REJECTED', 'DISCUSSION')


def notifications_badges(request):
    if not request.user.is_authenticated:
        return {
            'unread_notifications_count': 0,
            'my_open_issues_count': 0,
        }
    unread = Notification.objects.filter(
        recipient=request.user, is_read=False,
    ).count()
    my_open = request.user.assigned_issues.filter(
        status__in=ACTIVE_STATUSES,
        document__is_deleted=False,
    ).count() + request.user.created_issues.filter(
        status__in=ACTIVE_STATUSES,
        document__is_deleted=False,
    ).exclude(author_id=request.user.pk).count()
    return {
        'unread_notifications_count': unread,
        'my_open_issues_count': my_open,
    }
