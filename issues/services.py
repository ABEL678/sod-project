"""Сервисный слой замечаний (Этап A плана ISSUES_TAB_PLAN.md).

Перенос логики реестра из issues/views.py в публичный сервисный модуль.
Все функции — project-scoped (принимают user + project), не per-document.
"""

from django.db.models import Count, Q
from django.utils import timezone

from storage.domain.services import get_readable_documents

from .models import Issue

# Группы статусов — те же, что в табах панели замечаний вьювера,
# чтобы «Открытые» означало одно и то же на обоих экранах.
ISSUE_STATUS_GROUPS = {
    'open': ('NEW', 'REJECTED', 'DISCUSSION'),
    'progress': ('IN_PROGRESS',),
    'review': ('DONE',),
    'closed': ('CLOSED', 'CANCELLED'),
}
ACTIVE_ISSUE_STATUSES = ISSUE_STATUS_GROUPS['open'] + ISSUE_STATUS_GROUPS['progress']


def _readable_document_pks(user, project):
    return list(
        get_readable_documents(user, project).values_list('pk', flat=True)
    )


def project_issues(user, project):
    """Все замечания проекта, видимые пользователю (по правам на документы)."""
    pks = _readable_document_pks(user, project)
    return Issue.objects.filter(document_id__in=pks).select_related(
        'document',
        'document__project',
        'document__section',
        'author',
        'assignee',
    )


def apply_issue_filters(queryset, params):
    """GET-фильтры реестра (status/flag/assignee/author/document/q).

    Параметры:
      status   — одиночный статус
      flag     — active/open/closed/overdue/progress/review/blocking
      assignee — pk или 'none'
      author   — pk или 'me'
      document — pk
      q        — поиск по тексту/коду/имени документа
    """
    status = (params.get('status') or '').strip()
    if status in dict(Issue.STATUS_CHOICES):
        queryset = queryset.filter(status=status)

    flag = (params.get('flag') or '').strip()
    if flag == 'active':
        queryset = queryset.filter(status__in=ACTIVE_ISSUE_STATUSES)
    elif flag == 'open':
        queryset = queryset.exclude(status__in=ISSUE_STATUS_GROUPS['closed'])
    elif flag == 'closed':
        queryset = queryset.filter(status__in=ISSUE_STATUS_GROUPS['closed'])
    elif flag == 'overdue':
        queryset = queryset.filter(
            due_date__isnull=False,
            due_date__lt=timezone.localdate(),
            status__in=ACTIVE_ISSUE_STATUSES,
        )
    elif flag == 'progress':
        queryset = queryset.filter(status__in=ISSUE_STATUS_GROUPS['progress'])
    elif flag == 'review':
        queryset = queryset.filter(status__in=ISSUE_STATUS_GROUPS['review'])
    elif flag == 'blocking':
        queryset = queryset.filter(
            Q(blocked_design_tasks__isnull=False)
            | Q(blocked_rfi_tasks__isnull=False)
        ).distinct()

    assignee = (params.get('assignee') or '').strip()
    if assignee == 'none':
        queryset = queryset.filter(assignee__isnull=True)
    elif assignee.isdigit():
        queryset = queryset.filter(assignee_id=int(assignee))

    author = (params.get('author') or '').strip()
    if author == 'me':
        queryset = queryset.filter(author=params.get('_user'))
    elif author.isdigit():
        queryset = queryset.filter(author_id=int(author))

    document = (params.get('document') or '').strip()
    if document.isdigit():
        queryset = queryset.filter(document_id=int(document))

    query = (params.get('q') or '').strip()
    if query:
        queryset = queryset.filter(
            Q(description__icontains=query)
            | Q(document__code__icontains=query)
            | Q(document__name__icontains=query)
        )
    return queryset


def issue_summary(user, project):
    """Сводка по замечаниям проекта: счётчики по группам + просрочка + blocking.

    Возвращает dict:
      total    — всего замечаний
      groups   — {group_name: count}
      open     — open + progress (активные)
      in_progress — IN_PROGRESS
      review   — DONE
      closed   — CLOSED + CANCELLED
      overdue  — просроченные активные
      blocking — замечания с непустыми M2M-связями (заглушка, всегда 0 пока нет миграции)
    """
    qs = project_issues(user, project)
    counts = dict(qs.values_list('status').annotate(total=Count('pk')))
    today = timezone.localdate()
    overdue = qs.filter(
        due_date__isnull=False,
        due_date__lt=today,
        status__in=ACTIVE_ISSUE_STATUSES,
    ).count()
    groups = {
        group: sum(counts.get(status, 0) for status in statuses)
        for group, statuses in ISSUE_STATUS_GROUPS.items()
    }
    return {
        'total': sum(counts.values()),
        'groups': groups,
        'active': groups['open'] + groups['progress'],
        'open': groups['open'],
        'in_progress': groups['progress'],
        'review': groups['review'],
        'closed': groups['closed'],
        'overdue': overdue,
        'blocking': qs.filter(
            Q(blocked_design_tasks__isnull=False)
            | Q(blocked_rfi_tasks__isnull=False)
        ).distinct().count(),
    }


def issues_requiring_attention(user, project):
    """Замечания, требующие внимания: просроченные, NEW без исполнителя, активные REJECTED.

    Возвращает список dict с ключами:
      issue, reason_label, reason_badge, is_overdue, overdue_days
    """
    today = timezone.localdate()
    qs = project_issues(user, project)
    overdue = qs.filter(
        due_date__isnull=False,
        due_date__lt=today,
        status__in=ACTIVE_ISSUE_STATUSES,
    )
    unassigned_new = qs.filter(status='NEW', assignee__isnull=True)
    active_rejected = qs.filter(status='REJECTED')
    combined = {}
    for issue in overdue:
        combined[issue.pk] = {
            'issue': issue,
            'reason_label': 'Просрочено',
            'reason_badge': 'text-bg-danger',
            'is_overdue': True,
            'overdue_days': (today - issue.due_date).days,
        }
    for issue in unassigned_new:
        if issue.pk not in combined:
            combined[issue.pk] = {
                'issue': issue,
                'reason_label': 'Нет ответственного',
                'reason_badge': 'text-bg-warning',
                'is_overdue': False,
                'overdue_days': 0,
            }
    for issue in active_rejected:
        if issue.pk not in combined:
            combined[issue.pk] = {
                'issue': issue,
                'reason_label': 'Не принято',
                'reason_badge': 'text-bg-secondary',
                'is_overdue': False,
                'overdue_days': 0,
            }
    return list(combined.values())


def blocking_links_map(user, project):
    """Собирает блокирующие связи по всем замечаниям проекта.

    Возвращает {issue_pk: {'design_tasks': [(pk, title, ...), ...],
                            'rfi_tasks': [(pk, subject, ...), ...]}}.
    """
    pks = _readable_document_pks(user, project)
    issues = Issue.objects.filter(document_id__in=pks).only('pk').prefetch_related(
        'blocked_design_tasks', 'blocked_rfi_tasks',
    )
    result = {}
    for issue in issues:
        dt = [
            (t.pk, t.title, t.number)
            for t in issue.blocked_design_tasks.all()
        ]
        rt = [
            (t.pk, t.subject, t.number)
            for t in issue.blocked_rfi_tasks.all()
        ]
        result[issue.pk] = {
            'design_tasks': dt,
            'rfi_tasks': rt,
        }
    return result