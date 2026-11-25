"""W2-1: уведомления по спорам вокруг замечаний.

Покрывает события:
- перевод замечания в DISCUSSION — автор получает ``discussion_opened``;
- смена блокирующего флага у замечания в DISCUSSION — обе стороны спора
  (автор и инициатор перевода) получают ``blocking_changed``;
- повторное событие не создаёт дубль, пока уведомление не прочитано;
- после прочтения новое событие уведомляет снова.

Сигналы только добавляют Notification: статусы, счётчики реестра и
итог A/B/C не меняются.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from issues.models import Issue, Notification
from issues.services import approval_outcome, set_issue_blocking
from storage.domain.models import (
    Company,
    ProjectMembership,
    Section,
    UserProfile,
)
from storage.domain.services import (
    create_issue,
    create_project,
    update_issue_status,
    upload_document,
)


User = get_user_model()


@override_settings(AI_CHECKS_ENABLED=False)
class IssueDisputeNotificationTests(TestCase):
    """События ``discussion_opened`` и ``blocking_changed``."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания W2-1')

        self.admin = User.objects.create_user('w2-admin', password='pass')
        UserProfile.objects.update_or_create(
            user=self.admin,
            defaults={'role': 'admin', 'company': self.company},
        )
        self.gip = User.objects.create_user('w2-gip', password='pass')
        UserProfile.objects.update_or_create(
            user=self.gip,
            defaults={'role': 'gip', 'company': self.company},
        )
        self.developer = User.objects.create_user(
            'w2-developer', password='pass',
        )
        UserProfile.objects.update_or_create(
            user=self.developer,
            defaults={'role': 'developer', 'company': self.company},
        )
        self.checker = User.objects.create_user('w2-checker', password='pass')
        UserProfile.objects.update_or_create(
            user=self.checker,
            defaults={'role': 'checker', 'company': self.company},
        )

        self.project = create_project(
            company=self.company,
            code='W2-01',
            name='Проект W2-1',
            customer_id=None,
            gip=self.gip,
            created_by=self.admin,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.developer,
            role_in_project='developer',
        )
        self.section = Section.objects.create(
            project=self.project,
            code='АР',
            name='Архитектурные решения',
            order=1,
        )
        self.document = upload_document(
            user=self.gip,
            project=self.project,
            section=self.section,
            code='АР-01',
            name='План',
            doc_type='drawing',
            file=SimpleUploadedFile('АР-01.pdf', b'%PDF-1.4 test'),
            reason_text='Новая редакция',
        ).document
        # Автор замечания — проверяющий; замечание сразу блокирующее.
        self.issue = create_issue(
            user=self.checker,
            document=self.document,
            description='Неверный узел опирания',
            is_blocking=True,
        )

    # --- helpers --------------------------------------------------------

    def _open_discussion(self):
        update_issue_status(self.developer, self.issue, 'DISCUSSION')
        self.issue.refresh_from_db()
        return self.issue

    def _blocking_events(self):
        return Notification.objects.filter(
            issue=self.issue, verb='blocking_changed',
        )

    def _discussion_events(self, recipient=None):
        queryset = Notification.objects.filter(
            issue=self.issue, verb='discussion_opened',
        )
        if recipient is not None:
            queryset = queryset.filter(recipient=recipient)
        return queryset

    # --- событие: перевод в DISCUSSION ---------------------------------

    def test_discussion_transition_notifies_author(self):
        self._open_discussion()

        notification = self._discussion_events(self.checker).get()
        self.assertEqual(notification.actor, self.developer)
        self.assertFalse(notification.is_read)
        self.assertEqual(notification.issue, self.issue)
        self.assertEqual(notification.document, self.document)
        self.assertEqual(notification.project, self.project)
        self.assertIn('обсуждение', notification.message)
        self.assertIn(f'№{self.issue.pk}', notification.message)
        self.assertIn('блокирующее', notification.message)
        self.assertEqual(
            notification.url,
            reverse('storage:document_detail', args=[self.document.pk]),
        )
        # Общий status_changed автору не дублируется: событие одно.
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.checker, issue=self.issue, is_read=False,
            ).count(),
            1,
        )

    def test_discussion_initiator_does_not_notify_itself(self):
        self._open_discussion()

        self.assertFalse(
            self._discussion_events(self.developer).exists(),
            'Инициатор перевода не получает уведомление о своём действии',
        )

    # --- событие: смена blocking в DISCUSSION ---------------------------

    def test_blocking_change_in_discussion_notifies_both_sides(self):
        self._open_discussion()

        set_issue_blocking(self.gip, self.issue, False, 'Спор снят ГИПом')

        events = self._blocking_events()
        self.assertEqual(events.count(), 2)
        self.assertEqual(
            set(events.values_list('recipient_id', flat=True)),
            {self.checker.pk, self.developer.pk},
        )
        for notification in events:
            self.assertEqual(notification.actor, self.gip)
            self.assertFalse(notification.is_read)
            self.assertIn('блокирующее → неблокирующее', notification.message)

    def test_blocking_change_outside_discussion_notifies_nobody(self):
        # NEW: замечание ещё не в споре — событие не создаётся.
        set_issue_blocking(self.gip, self.issue, False, 'Снят до обсуждения')

        self.assertFalse(self._blocking_events().exists())

    def test_blocking_change_either_direction_notifies(self):
        self._open_discussion()
        set_issue_blocking(self.gip, self.issue, False, 'Спор снят')
        self._blocking_events().update(is_read=True)
        self.issue.refresh_from_db()

        set_issue_blocking(self.gip, self.issue, True, 'Спор возвращён')

        events = self._blocking_events()
        self.assertEqual(events.count(), 4)
        latest = events.order_by('-pk').first()
        self.assertIn('неблокирующее → блокирующее', latest.message)
        self.assertEqual(
            set(
                events.filter(is_read=False).values_list(
                    'recipient_id', flat=True,
                )
            ),
            {self.checker.pk, self.developer.pk},
        )

    # --- дедупликация ---------------------------------------------------

    def test_repeated_discussion_event_does_not_duplicate_unread(self):
        self._open_discussion()
        update_issue_status(self.developer, self.issue, 'IN_PROGRESS')
        update_issue_status(self.developer, self.issue, 'DISCUSSION')

        self.assertEqual(self._discussion_events(self.checker).count(), 1)

    def test_repeated_blocking_change_does_not_duplicate_unread(self):
        self._open_discussion()
        set_issue_blocking(self.gip, self.issue, False, 'Спор снят')
        self.issue.refresh_from_db()
        set_issue_blocking(self.gip, self.issue, True, 'Спор возвращён')

        # Одно событие на получателя, несмотря на повторную смену флага.
        self.assertEqual(self._blocking_events().count(), 2)

    def test_read_notification_allows_new_event(self):
        self._open_discussion()
        Notification.objects.filter(
            recipient=self.checker, verb='discussion_opened',
        ).update(is_read=True)

        update_issue_status(self.developer, self.issue, 'IN_PROGRESS')
        update_issue_status(self.developer, self.issue, 'DISCUSSION')

        self.assertEqual(self._discussion_events(self.checker).count(), 2)

    # --- реестр и итог не затронуты -------------------------------------

    def test_notifications_do_not_change_outcome_or_status(self):
        self._open_discussion()
        self.assertEqual(approval_outcome(self.document), 'C')
        set_issue_blocking(self.gip, self.issue, False, 'Спор снят')

        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, 'DISCUSSION')
        self.assertFalse(self.issue.is_blocking)
        # Блокирующих нет, но открытое DISCUSSION остаётся: итог B.
        self.assertEqual(approval_outcome(self.document), 'B')
