from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from issues.models import Issue, IssueFlagLog
from issues.services import (
    APPROVAL_BLOCKED_MESSAGE,
    OPEN_ISSUE_STATUSES,
    apply_issue_filters,
    approval_outcome,
    project_issues,
    set_issue_blocking,
)
from storage.domain.models import (
    ChangeSetEntry,
    Company,
    Document,
    Folder,
    ProjectMembership,
    Section,
    UserProfile,
)
from storage.domain.services import (
    add_change_permission_document,
    approve_change_permission,
    can_transition,
    create_change_permission_draft,
    create_issue,
    create_project,
    document_internal_actions,
    document_workflow_actions,
    transition_document_zone,
    update_internal_status,
    upload_document,
    validate_transition,
)


User = get_user_model()


@override_settings(AI_CHECKS_ENABLED=False)
class ApprovalOutcomeABCTests(TestCase):
    """SPEC 2.0, волна 0, п. 6 — блокирующий флаг и итог A/B/C."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания согласования')
        self.admin = User.objects.create_user('abc-admin', password='pass')
        UserProfile.objects.update_or_create(
            user=self.admin,
            defaults={'role': 'admin', 'company': self.company},
        )
        self.gip = User.objects.create_user('abc-gip', password='pass')
        UserProfile.objects.update_or_create(
            user=self.gip,
            defaults={'role': 'gip', 'company': self.company},
        )
        self.author = User.objects.create_user('abc-author', password='pass')
        self.author_profile = UserProfile.objects.update_or_create(
            user=self.author,
            defaults={'role': 'developer', 'company': self.company},
        )[0]

        self.project = create_project(
            company=self.company,
            code='ABC-01',
            name='Проект A/B/C',
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
        self.author_profile.allowed_sections.add(self.section)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.author,
            role_in_project='developer',
        )
        self.published_folder = Folder.objects.get(
            project=self.project, zone='PUBLISHED',
        )

    def _upload(self, code='АР-01', user=None, folder=None,
                change_permission=None):
        return upload_document(
            user=user or self.gip,
            project=self.project,
            section=self.section,
            code=code,
            name='План',
            doc_type='drawing',
            file=SimpleUploadedFile(f'{code}.pdf', b'%PDF-1.4 test'),
            reason_text='Новая редакция',
            folder=folder,
            change_permission=change_permission,
        )

    def _ready_document(self, code='АР-01'):
        document = self._upload(code).document
        update_internal_status(self.gip, document, 'send_to_review')
        update_internal_status(self.gip, document, 'approve_internal')
        document.refresh_from_db()
        self.assertEqual(document.internal_status, 'ready_to_share')
        return document

    def _issue(self, document, description='Замечание', is_blocking=False,
               author=None):
        return create_issue(
            user=author or self.gip,
            document=document,
            description=description,
            is_blocking=is_blocking,
        )

    # --- итог A/B/C -----------------------------------------------------

    def test_outcome_a_then_b_then_c(self):
        document = self._ready_document()
        self.assertEqual(approval_outcome(document), 'A')

        non_blocking = self._issue(document, 'Незначительное замечание')
        self.assertEqual(approval_outcome(document), 'B')

        blocking = self._issue(
            document, 'Критичное замечание', is_blocking=True,
        )
        self.assertEqual(approval_outcome(document), 'C')

        blocking.status = 'CLOSED'
        blocking.save(update_fields=['status'])
        self.assertEqual(approval_outcome(document), 'B')

        non_blocking.status = 'CLOSED'
        non_blocking.save(update_fields=['status'])
        self.assertEqual(approval_outcome(document), 'A')

    def test_discussion_is_open_but_non_blocking(self):
        # SPEC 2.0 ред. 3: неблокирующее DISCUSSION — открытое, даёт B и не
        # мешает подаче. Блокирующее DISCUSSION даёт C (test_w1_open_set).
        document = self._ready_document()
        discussion = Issue.objects.create(
            document=document,
            author=self.gip,
            description='Спорный вопрос',
            status='DISCUSSION',
            is_blocking=False,
        )
        self.assertEqual(approval_outcome(document), 'B')

        transition_document_zone(self.gip, document, 'submit_review')
        document.refresh_from_db()
        self.assertEqual(document.status, 'in_review')
        self.assertFalse(discussion.is_blocking)

    # --- переходы: только C блокирует ----------------------------------

    def test_b_allows_submit_and_c_blocks(self):
        allowed = self._ready_document('АР-B')
        self._issue(allowed, 'Неблокирующее')
        transition_document_zone(self.gip, allowed, 'submit_review')
        allowed.refresh_from_db()
        self.assertEqual(allowed.status, 'in_review')

        blocked = self._ready_document('АР-C')
        self._issue(blocked, 'Блокирующее', is_blocking=True)
        with self.assertRaisesMessage(ValueError, APPROVAL_BLOCKED_MESSAGE):
            transition_document_zone(self.gip, blocked, 'submit_review')

    def test_can_and_validate_transition_use_outcome(self):
        document = self._ready_document()
        self._issue(document, 'Неблокирующее')
        transition_document_zone(self.gip, document, 'submit_review')
        document.refresh_from_db()
        self.assertEqual(document.status, 'in_review')
        self.assertTrue(
            can_transition(document, 'approved', self.gip, 'A')
        )

        self._issue(document, 'Блокирующее', is_blocking=True)
        self.assertFalse(
            can_transition(document, 'approved', self.gip, 'A')
        )
        with self.assertRaisesMessage(
            ValidationError, APPROVAL_BLOCKED_MESSAGE,
        ):
            validate_transition(document, 'approved', self.gip, 'A')

    def test_internal_actions_follow_outcome(self):
        document = self._upload('АР-INT').document
        update_internal_status(self.gip, document, 'send_to_review')
        self._issue(document, 'Блокирующее', is_blocking=True)

        codes = [a['code'] for a in document_internal_actions(self.gip, document)]
        self.assertNotIn('approve_internal', codes)
        with self.assertRaisesMessage(ValueError, APPROVAL_BLOCKED_MESSAGE):
            update_internal_status(self.gip, document, 'approve_internal')

        ready = self._ready_document('АР-READY')
        self._issue(ready, 'Блокирующее', is_blocking=True)
        workflow_codes = [
            a['code'] for a in document_workflow_actions(self.gip, ready)
        ]
        self.assertNotIn('submit_review', workflow_codes)

    def test_upload_to_published_blocks_only_c(self):
        # PUT в PUBLISHED: B разрешён по утверждённому РВИ, C запрещён.
        allowed = self._upload(
            'АР-PUB-B', user=self.admin, folder=self.published_folder,
        ).document
        self._issue(allowed, 'Неблокирующее')
        permission = create_change_permission_draft(
            self.gip,
            self.project,
            content='Новая редакция по неблокирующим замечаниям',
        )
        add_change_permission_document(
            self.gip, permission, allowed, sheets='Все',
        )
        approve_change_permission(self.gip, permission)
        self._upload(
            'АР-PUB-B', user=self.admin, folder=self.published_folder,
            change_permission=permission,
        )
        allowed.refresh_from_db()
        self.assertEqual(allowed.status, 'approved')

        blocked = self._upload(
            'АР-PUB-C', user=self.admin, folder=self.published_folder,
        ).document
        self._issue(blocked, 'Блокирующее', is_blocking=True)
        # Плечо C без РВИ: загрузку останавливает итог C, до проверки
        # основания изменения дело не доходит.
        with self.assertRaisesMessage(ValueError, APPROVAL_BLOCKED_MESSAGE):
            self._upload(
                'АР-PUB-C', user=self.admin, folder=self.published_folder,
            )

    # --- переопределение итога -----------------------------------------

    def test_override_only_gip_with_justification_and_changeset(self):
        document = self._ready_document()
        self._issue(document, 'Блокирующее', is_blocking=True)

        with self.assertRaises(PermissionDenied):
            transition_document_zone(
                self.admin, document, 'submit_review',
                override_justification='Админ не ГИП',
            )
        with self.assertRaisesMessage(
            ValueError, 'Обоснование переопределения обязательно',
        ):
            transition_document_zone(
                self.gip, document, 'submit_review',
                override_justification='   ',
            )

        transition_document_zone(
            self.gip, document, 'submit_review',
            override_justification='Согласовано ГИПом вопреки блокирующему',
        )
        document.refresh_from_db()
        self.assertTrue(document.outcome_override)
        self.assertEqual(document.outcome_override_by, self.gip)
        self.assertEqual(approval_outcome(document), 'B')
        self.assertEqual(document.status, 'in_review')

        entry = ChangeSetEntry.objects.get(
            version=document.current_version,
            entry_type='result_override',
        )
        self.assertEqual(
            entry.text, 'Согласовано ГИПом вопреки блокирующему',
        )
        self.assertEqual(entry.created_by, self.gip)

    def test_new_blocking_issue_resets_override(self):
        document = self._ready_document()
        self._issue(document, 'Блокирующее', is_blocking=True)
        transition_document_zone(
            self.gip, document, 'submit_review',
            override_justification='Первичное переопределение',
        )
        document.refresh_from_db()
        self.assertTrue(document.outcome_override)

        self._issue(document, 'Новое блокирующее', is_blocking=True)
        document.refresh_from_db()
        self.assertFalse(document.outcome_override)
        self.assertEqual(document.outcome_override_text, '')
        self.assertEqual(approval_outcome(document), 'C')

    # --- журнал смены флага --------------------------------------------

    def test_flag_change_journal_and_permissions(self):
        document = self._ready_document()
        issue = self._issue(document, 'Флаг', author=self.author)

        set_issue_blocking(
            self.author, issue, True, 'Замечание критично для выпуска',
        )
        issue.refresh_from_db()
        self.assertTrue(issue.is_blocking)
        log = IssueFlagLog.objects.get(issue=issue)
        self.assertFalse(log.old_value)
        self.assertTrue(log.new_value)
        self.assertEqual(log.changed_by, self.author)
        self.assertEqual(log.justification, 'Замечание критично для выпуска')

        with self.assertRaisesMessage(
            ValueError, 'Обоснование изменения флага обязательно',
        ):
            set_issue_blocking(self.author, issue, False, '  ')

        with self.assertRaises(PermissionDenied):
            set_issue_blocking(self.admin, issue, False, 'Не автор и не ГИП')

        set_issue_blocking(self.gip, issue, False, 'Снято ГИПом')
        self.assertEqual(IssueFlagLog.objects.filter(issue=issue).count(), 2)

    # --- каноническое множество в реестре ------------------------------

    def test_registry_open_uses_canonical_set(self):
        document = self._ready_document()
        for status in (
            'NEW', 'IN_PROGRESS', 'REJECTED', 'DISCUSSION', 'DONE', 'CLOSED',
        ):
            Issue.objects.create(
                document=document,
                author=self.gip,
                description=f'Замечание {status}',
                status=status,
            )

        self.assertEqual(
            OPEN_ISSUE_STATUSES, ('NEW', 'IN_PROGRESS', 'REJECTED'),
        )
        filtered = apply_issue_filters(
            project_issues(self.gip, self.project), {'flag': 'open'},
        )
        self.assertEqual(
            set(filtered.values_list('status', flat=True)),
            {'NEW', 'IN_PROGRESS', 'REJECTED', 'DISCUSSION'},
        )

    # --- UI -------------------------------------------------------------

    def test_document_card_shows_outcome_and_blocks_actions(self):
        document = self._ready_document()
        self._issue(document, 'Блокирующее', is_blocking=True)
        self.client.force_login(self.gip)

        response = self.client.get(
            reverse('storage:document_detail', args=[document.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Итог согласования')
        self.assertContains(response, 'C — не согласовано')
        self.assertContains(response, 'B вопреки блокирующим')

    def test_document_card_shows_b_badge(self):
        document = self._ready_document()
        self._issue(document, 'Неблокирующее')
        self.client.force_login(self.gip)

        response = self.client.get(
            reverse('storage:document_detail', args=[document.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'B — согласовано с замечаниями')
