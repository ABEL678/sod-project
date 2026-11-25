"""Уведомления (Волна 3): единая точка создания issues.Notification.

Вызывается из сервисного слоя storage (статусы/комментарии/назначения
замечаний, workflow зон ISO 19650). Правило одно: инициатор действия
(actor) уведомлений о своём действии не получает.
"""

from django.contrib.auth.models import User
from django.db.models import Q

from .models import Issue, Notification

# Глаголы, с которыми Notification связана с документом, а не замечанием
DOCUMENT_VERBS = {
    'submit_review': 'doc_submitted',
    'publish': 'doc_published',
    'return_wip': 'doc_returned',
    'archive': 'doc_archived',
}

WORKFLOW_MESSAGES = {
    'submit_review': 'подал(а) документ на согласование',
    'publish': 'опубликовал(а) документ',
    'return_wip': 'вернул(а) документ в работу',
    'archive': 'отправил(а) документ в архив',
}


def create_notification(
    recipient: User,
    actor: User | None,
    verb: str,
    issue: Issue | None = None,
    document=None,
    project=None,
    message: str = '',
    url: str = '',
    dedupe: bool = False,
) -> Notification | None:
    """Создаёт уведомление; сам актёру ничего не отправляется.

    ``dedupe`` — не создавать второе уведомление, если по паре
    (замечание, событие) у получателя уже есть непрочитанное.
    """
    if recipient is None or recipient_id(recipient) == actor_id(actor):
        return None
    try:
        if (
            dedupe
            and issue is not None
            and Notification.objects.filter(
                recipient=recipient,
                issue=issue,
                verb=verb,
                is_read=False,
            ).exists()
        ):
            return None
        notification = Notification.objects.create(
            recipient=recipient,
            actor=actor,
            issue=issue,
            document=document,
            project=project,
            verb=verb,
            message=message[:300],
            url=url[:300],
        )
        _dispatch_email(notification)
        return notification
    except Exception:  # pragma: no cover — уведомления не роняют основной сценарий
        return None


def _dispatch_email(notification: Notification) -> None:
    """Отправляет email через Celery (фоново), если у получателя есть email."""
    email = (notification.recipient.email or '').strip()
    if not email:
        return
    try:
        from storage.tasks import send_notification_email

        send_notification_email.delay(
            recipient_email=email,
            subject='Новое уведомление в Stream-CAD',
            message=(
                f'Здравствуйте!\n\n'
                f'{notification.message}\n\n'
                f'С уважением, Stream-CAD'
            ),
        )
    except Exception:  # pragma: no cover
        pass


def recipient_id(user: User | None):
    return user.pk if user is not None else None


def actor_id(user: User | None):
    return user.pk if user is not None else None


def issue_url(issue: Issue) -> str:
    try:
        from django.urls import reverse
        return reverse('storage:document_detail', args=[issue.document_id])
    except Exception:  # pragma: no cover
        return ''


def notify_issue_assigned(issue: Issue, actor: User | None) -> None:
    """Назначен ответственный — уведомляем его."""
    if issue.assignee_id is None:
        return
    create_notification(
        recipient=issue.assignee,
        actor=actor,
        verb='assigned',
        issue=issue,
        document=issue.document,
        project=issue.document.project,
        message='Вам назначено замечание №{} — {}'.format(
            issue.pk, issue.document.code
        ),
        url=issue_url(issue),
    )


def notify_issue_status_changed(
    issue: Issue,
    actor: User | None,
    old_status: str,
    exclude_user_ids: set[int] | None = None,
) -> None:
    """Смена статуса — уведомляем автора и ответственного (кроме актёра).

    ``exclude_user_ids`` — получатели, которым событие уже отправлено
    отдельным (семантическим) уведомлением; используется для перевода
    в DISCUSSION, чтобы автор не получал два уведомления об одном
    событии.
    """
    verb = 'status_changed'
    message = 'Статус замечания №{} ({}): {} → {}'.format(
        issue.pk,
        issue.document.code,
        dict(Issue.STATUS_CHOICES).get(old_status, old_status),
        issue.get_status_display(),
    )
    url = issue_url(issue)
    recipients = {issue.author_id: issue.author}
    if issue.assignee_id is not None:
        recipients[issue.assignee_id] = issue.assignee
    if exclude_user_ids:
        recipients = {
            user_id: recipient
            for user_id, recipient in recipients.items()
            if user_id not in exclude_user_ids
        }
    for recipient in recipients.values():
        create_notification(
            recipient=recipient,
            actor=actor,
            verb=verb,
            issue=issue,
            document=issue.document,
            project=issue.document.project,
            message=message,
            url=url,
        )


def _discussion_initiator(issue: Issue) -> User | None:
    """Кто последним перевёл замечание в DISCUSSION.

    Отдельной истории статусов у Issue нет, поэтому инициатор берётся из
    уже созданных событийных уведомлений: явного ``discussion_opened``
    или общего ``status_changed`` с целевым статусом «К обсуждению».
    """
    notification = (
        Notification.objects.filter(
            Q(verb='discussion_opened')
            | Q(verb='status_changed', message__contains='К обсуждению'),
            issue=issue,
            actor__isnull=False,
        )
        .select_related('actor')
        .order_by('-created_at', '-pk')
        .first()
    )
    return notification.actor if notification is not None else None


def notify_issue_discussion_opened(issue: Issue, actor: User | None) -> None:
    """Замечание переведено в DISCUSSION — уведомляем автора.

    Событие ``discussion_opened`` создаётся один раз на пару
    (замечание, событие), пока у получателя есть непрочитанное.
    Автор-инициатор уведомление о своём действии не получает — общее
    правило ``create_notification``.
    """
    if issue.author_id is None:
        return
    message = 'Замечание №{} ({}) переведено в обсуждение{}'.format(
        issue.pk,
        issue.document.code,
        ' — блокирующее' if issue.is_blocking else '',
    )
    create_notification(
        recipient=issue.author,
        actor=actor,
        verb='discussion_opened',
        issue=issue,
        document=issue.document,
        project=issue.document.project,
        message=message,
        url=issue_url(issue),
        dedupe=True,
    )


def notify_issue_blocking_changed(
    issue: Issue,
    actor: User | None,
    old_value: bool,
    new_value: bool,
) -> None:
    """У замечания в DISCUSSION изменён блокирующий флаг.

    Получатели — обе стороны спора: автор замечания и инициатор перевода
    в DISCUSSION. Актёр своего действия уведомление не получает.
    Событие ``blocking_changed`` одно на пару (замечание, событие):
    повторная смена при непрочитанном уведомлении дубль не создаёт.
    """
    if issue.status != 'DISCUSSION':
        return
    recipients: dict[int, User] = {}
    if issue.author_id is not None:
        recipients[issue.author_id] = issue.author
    initiator = _discussion_initiator(issue)
    if initiator is not None:
        recipients.setdefault(initiator.pk, initiator)

    message = 'Блокирующий флаг замечания №{} ({}): {} → {}'.format(
        issue.pk,
        issue.document.code,
        'блокирующее' if old_value else 'неблокирующее',
        'блокирующее' if new_value else 'неблокирующее',
    )
    url = issue_url(issue)
    for recipient in recipients.values():
        create_notification(
            recipient=recipient,
            actor=actor,
            verb='blocking_changed',
            issue=issue,
            document=issue.document,
            project=issue.document.project,
            message=message,
            url=url,
            dedupe=True,
        )


def notify_issue_commented(issue: Issue, actor: User | None) -> None:
    """Комментарий — уведомляем автора, ответственного и комментаторов."""
    message = 'Новый комментарий к замечанию №{} ({})'.format(
        issue.pk, issue.document.code
    )
    url = issue_url(issue)
    recipients = {}
    if issue.author_id is not None:
        recipients[issue.author_id] = issue.author
    if issue.assignee_id is not None:
        recipients[issue.assignee_id] = issue.assignee
    try:
        for commenter in issue.comments.values_list('author_id', flat=True):
            recipients.setdefault(commenter, User(pk=commenter))
    except Exception:  # pragma: no cover
        pass
    for recipient in recipients.values():
        create_notification(
            recipient=recipient,
            actor=actor,
            verb='commented',
            issue=issue,
            document=issue.document,
            project=issue.document.project,
            message=message,
            url=url,
        )


def notify_document_workflow(
    document,
    action_code: str,
    actor: User | None,
) -> None:
    """Workflow зон ISO 19650: подача/публикация/возврат/архив.

    - подача на согласование → ГИПам проекта;
    - публикация / возврат в работу / архив → автору документа и
      загрузчику текущей версии.
    """
    verb = DOCUMENT_VERBS.get(action_code)
    if verb is None:
        return
    message = '{} {}: «{}»'.format(
        actor.get_full_name() or (actor.username if actor else 'Кто-то'),
        WORKFLOW_MESSAGES.get(action_code, action_code),
        document.code,
    )
    url = ''
    try:
        from django.urls import reverse
        url = reverse('storage:document_detail', args=[document.pk])
    except Exception:  # pragma: no cover
        pass

    recipients: dict[int, User] = {}

    if action_code == 'submit_review':
        for membership in document.project.memberships.filter(
            role_in_project='gip'
        ).select_related('user'):
            recipients[membership.user_id] = membership.user
    else:
        if document.created_by_id is not None:
            recipients[document.created_by_id] = document.created_by
        version = document.current_version
        if version is not None and version.uploaded_by_id is not None:
            recipients.setdefault(
                version.uploaded_by_id, version.uploaded_by
            )

    for recipient in recipients.values():
        create_notification(
            recipient=recipient,
            actor=actor,
            verb=verb,
            document=document,
            project=document.project,
            message=message,
            url=url,
        )
