"""Ф4: точечные тесты напоминаний (check_reminders_task, дубли)."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from ai_checks.domain.models import (
    CheckRun,
    PendingCheckResult,
    ReminderRule,
    RuleDefinition,
)
from ai_checks.tasks import check_reminders_task
from issues.models import Notification
from storage.domain.models import (
    Company,
    Document,
    DocumentVersion,
    Project,
    ProjectMembership,
    Section,
)


class CheckRemindersTests(TestCase):
    def setUp(self):
        self.gip = User.objects.create_user(
            username='proj_gip', password='TestPass123',
        )
        self.gip.userprofile.role = 'gip'
        self.gip.userprofile.save()
        self.company = Company.objects.create(name='Напоминания-Компания')
        self.project = Project.objects.create(
            company=self.company, code='REM-1', name='Проект напоминаний',
            status='active', created_by=self.gip, gip=self.gip,
        )
        self.section = Section.objects.create(
            project=self.project, code='АР', name='Архитектура', order=1,
        )
        self.document = Document.objects.create(
            project=self.project, section=self.section,
            code='REM-DOC', name='Лист напоминаний',
            doc_type='drawing', status='draft', created_by=self.gip,
        )
        self.version = DocumentVersion.objects.create(
            document=self.document,
            version_number=1,
            file='documents/test/rem.pdf',
            file_size=100,
            uploaded_by=self.gip,
        )
        self.rule = RuleDefinition.objects.get(code='SPDS.RAME')

    def _finding(self, age_days: int, status='pending'):
        check_run = CheckRun.objects.create(
            document_version=self.version,
            initiated_by=self.gip,
            status=CheckRun.Status.DONE,
        )
        # created_at — auto_now_add: сдвигаем назад queryset-ом.
        CheckRun.objects.filter(pk=check_run.pk).update(
            created_at=timezone.now() - timedelta(days=age_days),
        )
        finding = PendingCheckResult.objects.create(
            check_run=check_run,
            rule=self.rule,
            status=status,
            severity=self.rule.severity,
            page=1,
            message='Незавершённая находка',
        )
        return finding

    def tearDown(self):
        pass

    def test_rule_seeded(self):
        self.assertTrue(
            ReminderRule.objects.filter(code='CHECK_PENDING_7').exists(),
        )

    def test_old_pending_finding_notifies_gip(self):
        finding = self._finding(age_days=10)
        created = check_reminders_task()
        self.assertEqual(created, 1)
        notification = Notification.objects.get(recipient=self.gip)
        self.assertEqual(notification.verb, 'ai_reminder')
        self.assertIn('Незавершённая ИИ-проверка', notification.message)
        self.assertIn(
            f'#ai-finding-{finding.pk}', notification.url,
        )
        self.assertEqual(notification.project, self.project)
        self.assertEqual(notification.document, self.document)

    def test_fresh_finding_not_notified(self):
        self._finding(age_days=1)
        self.assertEqual(check_reminders_task(), 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_reviewed_or_confirmed_not_notified(self):
        self._finding(age_days=10, status='rejected')
        self._finding(age_days=10, status='confirmed')
        self.assertEqual(check_reminders_task(), 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_no_duplicates_on_repeat_run(self):
        self._finding(age_days=10)
        first = check_reminders_task()
        second = check_reminders_task()
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(Notification.objects.count(), 1)

    def test_inactive_rule_skips(self):
        ReminderRule.objects.filter(code='CHECK_PENDING_7').update(
            is_active=False,
        )
        self._finding(age_days=10)
        self.assertEqual(check_reminders_task(), 0)

    def test_membership_gip_also_notified(self):
        self.project.gip = None
        self.project.save(update_fields=['gip'])
        second_gip = User.objects.create_user(
            username='member_gip', password='TestPass123',
        )
        ProjectMembership.objects.create(
            project=self.project, user=second_gip, role_in_project='gip',
        )
        self._finding(age_days=10)
        self.assertEqual(check_reminders_task(), 1)
        self.assertTrue(
            Notification.objects.filter(recipient=second_gip).exists(),
        )
