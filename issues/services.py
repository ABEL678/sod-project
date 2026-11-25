"""Сервисный слой замечаний (Этап A плана ISSUES_TAB_PLAN.md).

Перенос логики реестра из issues/views.py в публичный сервисный модуль.
Все функции — project-scoped (принимают user + project), не per-document.
"""

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import Issue, IssueFlagLog

# Единственное каноническое множество «открытых» замечаний для итога A/B/C
# (SPEC 2.0, волна 0, п. 6; решение владельца). Все потребители импортируют
# эту константу, локальные дубли запрещены.
OPEN_ISSUE_STATUSES = ('NEW', 'IN_PROGRESS', 'REJECTED')
# Спор (DISCUSSION) считается открытым, но НЕ блокирующим: в реестре — среди
# открытых, в итоге A/B/C — не даёт C.
DISCUSSION_ISSUE_STATUSES = ('DISCUSSION',)
ACTIVE_ISSUE_STATUSES = OPEN_ISSUE_STATUSES + DISCUSSION_ISSUE_STATUSES

APPROVAL_OUTCOME_A = 'A'
APPROVAL_OUTCOME_B = 'B'
APPROVAL_OUTCOME_C = 'C'

APPROVAL_BLOCKED_MESSAGE = (
    'Документ не согласован: есть блокирующие замечания (C)'
)

# Группы статусов — те же, что в табах панели замечаний вьювера,
# чтобы «Открытые» означало одно и то же на обоих экранах.
ISSUE_STATUS_GROUPS = {
    'open': ('NEW', 'REJECTED') + DISCUSSION_ISSUE_STATUSES,
    'progress': ('IN_PROGRESS',),
    'review': ('DONE',),
    'closed': ('CLOSED', 'CANCELLED'),
}


def _readable_document_pks(user, project):
    # Ленивый импорт: storage.domain.services сам импортирует константы
    # этого модуля (единое множество открытых), поэтому встречный импорт
    # на уровне модуля создал бы цикл.
    from storage.domain.services import get_readable_documents

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


def _single(params, key):
    """Возвращает одиночное строковое значение параметра.

    Устойчиво к QueryDict, dict и dict-of-lists (dict(QueryDict) даёт
    списки значений — фильтры не должны на этом падать).
    """
    value = params.get(key)
    if value is None:
        return ''
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ''
    return str(value).strip()


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
    status = _single(params, 'status')
    if status in dict(Issue.STATUS_CHOICES):
        queryset = queryset.filter(status=status)

    flag = _single(params, 'flag')
    if flag == 'active':
        queryset = queryset.filter(status__in=ACTIVE_ISSUE_STATUSES)
    elif flag == 'open':
        # Каноническое «открытые»: NEW/IN_PROGRESS/REJECTED + DISCUSSION.
        # DONE к открытым не относится (раньше ошибочно попадал через
        # exclude(closed)).
        queryset = queryset.filter(status__in=ACTIVE_ISSUE_STATUSES)
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
            Q(is_blocking=True)
            | Q(blocked_design_tasks__isnull=False)
            | Q(blocked_rfi_tasks__isnull=False)
        ).distinct()

    assignee = _single(params, 'assignee')
    if assignee == 'none':
        queryset = queryset.filter(assignee__isnull=True)
    elif assignee.isdigit():
        queryset = queryset.filter(assignee_id=int(assignee))

    author = _single(params, 'author')
    if author == 'me':
        queryset = queryset.filter(author=params.get('_user'))
    elif author.isdigit():
        queryset = queryset.filter(author_id=int(author))

    document = _single(params, 'document')
    if document.isdigit():
        queryset = queryset.filter(document_id=int(document))

    query = _single(params, 'q')
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
      blocking — замечания, блокирующие работу: флаг is_blocking или
                непустые M2M-связи (задания/RFI). Учитываются независимо
                от статуса, в том числе закрытые — в отличие от итога
                A/B/C, который смотрит только на открытые.
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
        # Активные/открытые — строго каноническое множество, не сумма
        # групп: IN_PROGRESS входит и в группу «open», и в «progress».
        'active': sum(counts.get(status, 0) for status in ACTIVE_ISSUE_STATUSES),
        'open': groups['open'],
        'in_progress': groups['progress'],
        'review': groups['review'],
        'closed': groups['closed'],
        'overdue': overdue,
        'blocking': qs.filter(
            Q(is_blocking=True)
            | Q(blocked_design_tasks__isnull=False)
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


def outcome_override_active(document) -> bool:
    """Активно ли ручное переопределение итога (B вопреки блокирующим)."""
    return bool(getattr(document, 'outcome_override', False))


def approval_outcome(document) -> str:
    """Итог согласования документа: 'A' / 'B' / 'C' (SPEC 2.0 w0 п. 6).

    A — нет открытых замечаний;
    B — открыты только неблокирующие (включая DISCUSSION);
    C — есть открытое замечание с блокирующим флагом.

    Активное переопределение ГИПа превращает C в B. Annotations
    ``open_issues_count`` / ``blocking_issues_count`` используются, если
    queryset уже их посчитал (таблица документов), иначе — два запроса.
    """
    open_count = getattr(document, 'open_issues_count', None)
    blocking_count = getattr(document, 'blocking_issues_count', None)
    if open_count is not None:
        if not open_count:
            return APPROVAL_OUTCOME_A
        if blocking_count is None:
            blocking_count = document.issues.filter(
                status__in=OPEN_ISSUE_STATUSES,
                is_blocking=True,
            ).count()
    else:
        open_qs = document.issues.filter(status__in=ACTIVE_ISSUE_STATUSES)
        if not open_qs.exists():
            return APPROVAL_OUTCOME_A
        blocking_count = open_qs.filter(
            status__in=OPEN_ISSUE_STATUSES,
            is_blocking=True,
        ).count()

    if blocking_count > 0 and not outcome_override_active(document):
        return APPROVAL_OUTCOME_C
    return APPROVAL_OUTCOME_B


def blocking_issues_for_document(document):
    """Открытые замечания с блокирующим флагом (для карточки и итога C)."""
    return (
        document.issues.filter(
            status__in=OPEN_ISSUE_STATUSES,
            is_blocking=True,
        )
        .select_related('author')
        .order_by('pk')
    )


def can_change_issue_blocking(user, issue) -> bool:
    """Право смены блокирующего флага: автор замечания или ГИП проекта."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if issue.author_id == user.pk:
        return True
    from storage.domain.services import _is_project_gip

    return _is_project_gip(user, issue.document.project)


def set_issue_blocking(user, issue, is_blocking: bool, justification: str):
    """Меняет блокирующий флаг замечания (SPEC 2.0 w0 п. 6).

    Право: автор замечания или ГИП проекта. Обоснование обязательно.
    Переключение и запись в IssueFlagLog атомарны. Установка флага
    блокирующим сбрасывает активное переопределение итога документа —
    новое блокирующее замечание отменяет прежнее «B вопреки блокирующим».
    """
    if not can_change_issue_blocking(user, issue):
        raise PermissionDenied(
            "Менять блокирующий флаг может автор замечания или ГИП проекта"
        )
    justification = (justification or '').strip()
    if not justification:
        raise ValueError("Обоснование изменения флага обязательно")

    new_value = bool(is_blocking)
    old_value = bool(issue.is_blocking)
    if old_value == new_value:
        return issue

    from storage.domain.services import _clear_outcome_override

    with transaction.atomic():
        issue.is_blocking = new_value
        issue.save(update_fields=['is_blocking', 'updated_at'])
        IssueFlagLog.objects.create(
            issue=issue,
            old_value=old_value,
            new_value=new_value,
            justification=justification,
            changed_by=user,
        )
        if new_value:
            _clear_outcome_override(issue.document)
    return issue


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