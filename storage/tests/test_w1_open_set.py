"""SPEC 2.0 ред. 3 (W1-1): блокирующее множество итога C.

DISCUSSION — открытое состояние (реестр, бейдж, рассылки, отчёты), и
блокирующее замечание в DISCUSSION участвует в итоге C наравне с
NEW/IN_PROGRESS/REJECTED. Переопределение итога (C→B) не меняется.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from issues.models import Issue
from issues.services import (
    apply_issue_filters,
    approval_outcome,
    issue_summary,
    project_issues,
    set_issue_blocking,
)
from storage.domain.models import (
    ChangeSetEntry,
    Company,
    Section,
    UserProfile,
)
from storage.domain.services import (
    create_issue,
    create_project,
    document_internal_actions,
    document_workflow_actions,
    transition_document_zone,
    update_internal_status,
    update_issue_status,
    upload_document,
)


User = get_user_model()


@override_settings(AI_CHECKS_ENABLED=False)
class OpenSetBlockingTests(TestCase):
    """W1-1: «открытые» = NEW/IN_PROGRESS/REJECTED + DISCUSSION."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания W1-1')
        self.admin = User.objects.create_user('w1-admin', password='pass')
        UserProfile.objects.update_or_create(
            user=self.admin,
            defaults={'role': 'admin', 'company': self.company},
        )
        self.gip = User.objects.create_user('w1-gip', password='pass')
        UserProfile.objects.update_or_create(
            user=self.gip,
            defaults={'role': 'gip', 'company': self.company},
        )
        self.project = create_project(
            company=self.company,
            code='W1-01',
            name='Проект W1-1',
            customer_id=None,
            gip=self.gip,
            created_by=self.admin,
        )
        self.section = Section.objects.create(
            project=self.project,
            code='АР',
            name='Архитектурные решения',
            order=1,
        )

    # --- helpers --------------------------------------------------------

    def _upload(self, code='АР-01'):
        return upload_document(
            user=self.gip,
            project=self.project,
            section=self.section,
            code=code,
            name='План',
            doc_type='drawing',
            file=SimpleUploadedFile(f'{code}.pdf', b'%PDF-1.4 test'),
            reason_text='Новая редакция',
        )

    def _ready_document(self, code='АР-01'):
        document = self._upload(code).document
        update_internal_status(self.gip, document, 'send_to_review')
        update_internal_status(self.gip, document, 'approve_internal')
        document.refresh_from_db()
        self.assertEqual(document.internal_status, 'ready_to_share')
        return document

    def _issue(self, document, description='Замечание', is_blocking=False):
        return create_issue(
            user=self.gip,
            document=document,
            description=description,
            is_blocking=is_blocking,
        )

    def _discussion(self, document, description='Спор', is_blocking=False):
        return Issue.objects.create(
            document=document,
            author=self.gip,
            description=description,
            status='DISCUSSION',
            is_blocking=is_blocking,
        )

    # --- итог C ---------------------------------------------------------

    def test_blocking_discussion_gives_outcome_c(self):
        document = self._ready_document()
        blocking = self._discussion(document, is_blocking=True)

        self.assertEqual(approval_outcome(document), 'C')
        self.assertTrue(blocking.is_blocking)

    def test_blocking_discussion_hides_submit_approve_and_agree(self):
        # «Согласовать» (approve_internal) — документ на внутренней проверке.
        reviewing = self._upload('АР-INT').document
        update_internal_status(self.gip, reviewing, 'send_to_review')
        self._discussion(reviewing, is_blocking=True)
        internal_codes = [
            action['code']
            for action in document_internal_actions(self.gip, reviewing)
        ]
        self.assertNotIn('approve_internal', internal_codes)

        # «Подать на согласование» — готовый к передаче документ в WIP.
        ready = self._ready_document('АР-READY')
        self._discussion(ready, is_blocking=True)
        workflow_codes = [
            action['code']
            for action in document_workflow_actions(self.gip, ready)
        ]
        self.assertNotIn('submit_review', workflow_codes)

        # «Утвердить» (publish) — документ уже в SHARED.
        shared = self._ready_document('АР-SHARED')
        transition_document_zone(self.gip, shared, 'submit_review')
        shared.refresh_from_db()
        self.assertEqual(shared.status, 'in_review')
        self._discussion(shared, is_blocking=True)
        shared_codes = [
            action['code']
            for action in document_workflow_actions(self.gip, shared)
        ]
        self.assertNotIn('publish', shared_codes)

    def test_blocking_discussion_to_in_progress_keeps_outcome_c(self):
        document = self._ready_document()
        blocking = self._discussion(document, is_blocking=True)
        self.assertEqual(approval_outcome(document), 'C')

        update_issue_status(self.gip, blocking, 'IN_PROGRESS')
        blocking.refresh_from_db()
        self.assertEqual(blocking.status, 'IN_PROGRESS')
        self.assertEqual(approval_outcome(document), 'C')

    def test_closing_blocking_discussion_recomputes_outcome(self):
        document = self._ready_document()
        self._issue(document, 'Неблокирующее')  # NEW → остаётся открытым
        blocking = self._discussion(document, is_blocking=True)
        self.assertEqual(approval_outcome(document), 'C')

        update_issue_status(self.gip, blocking, 'CLOSED')
        blocking.refresh_from_db()
        self.assertEqual(blocking.status, 'CLOSED')
        self.assertEqual(approval_outcome(document), 'B')

    # --- переопределение итога -----------------------------------------

    def test_override_with_blocking_discussion(self):
        document = self._ready_document()
        self._discussion(document, is_blocking=True)
        self.assertEqual(approval_outcome(document), 'C')

        transition_document_zone(
            self.gip,
            document,
            'submit_review',
            override_justification='Согласовано ГИПом вопреки обсуждению',
        )
        document.refresh_from_db()

        self.assertTrue(document.outcome_override)
        self.assertEqual(approval_outcome(document), 'B')
        self.assertTrue(
            ChangeSetEntry.objects.filter(
                version=document.current_version,
                entry_type='result_override',
                text='Согласовано ГИПом вопреки обсуждению',
            ).exists()
        )

    def test_new_blocking_discussion_resets_override(self):
        document = self._ready_document()
        self._discussion(document, is_blocking=True)
        transition_document_zone(
            self.gip,
            document,
            'submit_review',
            override_justification='Первичное переопределение',
        )
        document.refresh_from_db()
        self.assertTrue(document.outcome_override)
        self.assertEqual(approval_outcome(document), 'B')

        new_discussion = self._discussion(
            document, description='Новое блокирующее в обсуждении',
        )
        set_issue_blocking(
            self.gip,
            new_discussion,
            True,
            'Спор блокирует утверждение',
        )
        document.refresh_from_db()

        self.assertFalse(document.outcome_override)
        self.assertEqual(document.outcome_override_text, '')
        self.assertEqual(approval_outcome(document), 'C')

    # --- реестр не задет ------------------------------------------------

    def test_non_blocking_discussion_keeps_b_and_increments_open(self):
        document = self._ready_document()
        self._issue(document, 'Неблокирующее')
        before = issue_summary(self.gip, self.project)
        self.assertEqual(approval_outcome(document), 'B')

        discussion = self._discussion(document)
        after = issue_summary(self.gip, self.project)

        self.assertEqual(approval_outcome(document), 'B')
        self.assertEqual(after['open'], before['open'] + 1)
        self.assertEqual(after['active'], before['active'] + 1)
        registry = apply_issue_filters(
            project_issues(self.gip, self.project), {'flag': 'open'},
        )
        self.assertIn(discussion.pk, set(registry.values_list('pk', flat=True)))
