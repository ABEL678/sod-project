from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from storage.domain.models import (
    ChangePermission,
    ChangePermissionDocument,
    Company,
    Document,
    Section,
    UserProfile,
)
from storage.domain.services import (
    add_change_permission_document,
    approve_change_permission,
    can_approve_change_permission,
    check_issue_permission,
    check_read_permission,
    create_change_permission_draft,
    create_project,
    get_unapplied_change_permission,
    mark_change_permission_applied,
    remove_change_permission_document,
    update_change_permission_draft,
)


User = get_user_model()


class ChangePermissionTestBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='РВИ Организация')
        self.admin = User.objects.create_user(
            username='rvi_admin', password='pass123',
        )
        UserProfile.objects.create(
            user=self.admin, role='admin', company=self.company,
        )
        self.gip = User.objects.create_user(
            username='rvi_gip', password='pass123',
        )
        UserProfile.objects.create(
            user=self.gip, role='gip', company=self.company,
        )
        self.manager = User.objects.create_user(
            username='rvi_manager', password='pass123',
        )
        UserProfile.objects.create(
            user=self.manager, role='manager', company=self.company,
        )
        self.viewer = User.objects.create_user(
            username='rvi_viewer', password='pass123',
        )
        UserProfile.objects.create(
            user=self.viewer, role='viewer', company=self.company,
        )
        self.outsider = User.objects.create_user(
            username='rvi_outsider', password='pass123',
        )
        UserProfile.objects.create(
            user=self.outsider, role='developer', company=self.company,
        )
        self.project = create_project(
            company=self.company,
            code='RVI-001',
            name='Объект РВИ',
            gip=self.gip,
            created_by=self.admin,
        )
        self.section = Section.objects.create(
            project=self.project,
            code='АР',
            name='Архитектурные решения',
            order=1,
        )
        self.wip_document = self._document(
            'RVI-WIP', Document.STATE_WIP_DRAFT,
        )
        self.archive_document = self._document(
            'RVI-ARCH', Document.STATE_ARCHIVED,
        )

    def _document(self, code, state):
        return Document.objects.create(
            project=self.project,
            section=self.section,
            code=code,
            name=f'Документ {code}',
            doc_type='drawing',
            state=state,
            created_by=self.admin,
        )

    def _draft(self, content='Изменить планировку', document=None):
        permission = create_change_permission_draft(
            self.gip,
            self.project,
            content=content,
            reason_code='1',
            note='Примечание',
        )
        add_change_permission_document(
            self.gip,
            permission,
            document or self.wip_document,
            change_number=1,
            sheets='Лист 1',
        )
        return permission


class ChangePermissionDraftTests(ChangePermissionTestBase):
    def test_gip_creates_draft_without_code(self):
        permission = create_change_permission_draft(
            self.gip, self.project, content='Содержание изменения',
        )

        self.assertEqual(permission.status, ChangePermission.STATUS_DRAFT)
        self.assertIsNone(permission.code)
        self.assertEqual(permission.author, self.gip)
        self.assertEqual(permission.organization, self.company)

    def test_non_gip_cannot_create_draft(self):
        for user in (self.manager, self.viewer, self.outsider):
            with self.subTest(role=user.userprofile.role):
                with self.assertRaises(PermissionDenied):
                    create_change_permission_draft(
                        user, self.project, content='Черновик',
                    )

    def test_registry_assigns_sequential_codes(self):
        first = self._draft(document=self.wip_document)
        second = self._draft(
            content='Изменить фасад', document=self.archive_document,
        )

        approve_change_permission(self.gip, first)
        approve_change_permission(self.manager, second)
        first.refresh_from_db()
        second.refresh_from_db()

        year = timezone.localdate().year
        self.assertEqual(first.code, f'1-{year}')
        self.assertEqual(second.code, f'2-{year}')
        self.assertNotEqual(first.code, second.code)


class ChangePermissionApprovalMatrixTests(ChangePermissionTestBase):
    def test_gip_cannot_approve_when_document_in_archive(self):
        permission = self._draft(document=self.archive_document)

        self.assertFalse(
            can_approve_change_permission(self.gip, permission)
        )
        with self.assertRaises(PermissionDenied):
            approve_change_permission(self.gip, permission)

        permission.refresh_from_db()
        self.assertEqual(permission.status, ChangePermission.STATUS_DRAFT)
        self.assertIsNone(permission.code)

    def test_manager_approves_when_document_in_archive(self):
        permission = self._draft(document=self.archive_document)

        self.assertTrue(
            can_approve_change_permission(self.manager, permission)
        )
        approve_change_permission(self.manager, permission)

        permission.refresh_from_db()
        self.assertEqual(permission.status, ChangePermission.STATUS_APPROVED)
        self.assertEqual(permission.approved_by, self.manager)
        self.assertIsNotNone(permission.approved_at)
        self.assertIsNotNone(permission.code)

    def test_gip_approves_when_all_documents_outside_archive(self):
        permission = self._draft(document=self.wip_document)

        self.assertTrue(
            can_approve_change_permission(self.gip, permission)
        )
        approve_change_permission(self.gip, permission)

        permission.refresh_from_db()
        self.assertEqual(permission.status, ChangePermission.STATUS_APPROVED)
        self.assertEqual(permission.approved_by, self.gip)

    def test_viewer_and_non_participant_cannot_approve(self):
        permission = self._draft(document=self.wip_document)

        for user in (self.viewer, self.outsider):
            with self.subTest(role=user.userprofile.role):
                self.assertFalse(
                    can_approve_change_permission(user, permission)
                )
                with self.assertRaises(PermissionDenied):
                    approve_change_permission(user, permission)

        permission.refresh_from_db()
        self.assertEqual(permission.status, ChangePermission.STATUS_DRAFT)

    def test_manager_reads_organization_documents_and_creates_issues(self):
        self.assertTrue(
            check_read_permission(self.manager, self.archive_document)
        )
        self.assertTrue(
            check_issue_permission(self.manager, self.archive_document)
        )


class ChangePermissionImmutabilityTests(ChangePermissionTestBase):
    def setUp(self):
        super().setUp()
        self.permission = self._draft()
        approve_change_permission(self.manager, self.permission)
        self.permission.refresh_from_db()
        self.link = self.permission.document_links.get()

    def test_content_update_is_forbidden(self):
        with self.assertRaises(ValidationError):
            update_change_permission_draft(
                self.gip, self.permission, content='Новое содержание',
            )

    def test_direct_content_save_is_forbidden(self):
        self.permission.content = 'Новое содержание'
        with self.assertRaises(ValidationError):
            self.permission.save()

    def test_adding_document_is_forbidden(self):
        another = self._document('RVI-EXTRA', Document.STATE_WIP_DRAFT)
        with self.assertRaises(ValidationError):
            add_change_permission_document(
                self.gip, self.permission, another, change_number=2,
            )

    def test_direct_link_change_is_forbidden(self):
        self.link.change_number = 5
        with self.assertRaises(ValidationError):
            self.link.save()

    def test_removing_document_is_forbidden(self):
        with self.assertRaises(ValidationError):
            remove_change_permission_document(
                self.gip, self.permission, self.wip_document,
            )

    def test_direct_link_delete_is_forbidden(self):
        with self.assertRaises(ValidationError):
            self.link.delete()

    def test_direct_new_link_to_approved_is_forbidden(self):
        another = self._document('RVI-NEW-LINK', Document.STATE_WIP_DRAFT)
        with self.assertRaises(ValidationError):
            ChangePermissionDocument.objects.create(
                permission=self.permission,
                document=another,
                change_number=2,
            )


class ChangePermissionDocumentsTests(ChangePermissionTestBase):
    def test_one_permission_links_two_documents_with_own_numbers(self):
        permission = create_change_permission_draft(
            self.gip, self.project, content='Общее изменение',
        )
        first = add_change_permission_document(
            self.gip, permission, self.wip_document,
            change_number=1, sheets='Лист 1',
        )
        second = add_change_permission_document(
            self.gip, permission, self.archive_document,
            change_number=2, sheets='Все',
        )

        self.assertEqual(first.change_number, 1)
        self.assertEqual(first.sheets, 'Лист 1')
        self.assertEqual(second.change_number, 2)
        self.assertEqual(second.sheets, 'Все')
        self.assertEqual(permission.document_links.count(), 2)

    def test_duplicate_document_is_rejected(self):
        permission = self._draft()

        with self.assertRaises(ValueError):
            add_change_permission_document(
                self.gip, permission, self.wip_document,
                change_number=2,
            )

        self.assertEqual(permission.document_links.count(), 1)


class ChangePermissionLookupTests(ChangePermissionTestBase):
    def test_draft_is_not_returned(self):
        permission = self._draft()

        self.assertIsNone(
            get_unapplied_change_permission(self.wip_document)
        )
        self.assertIsNone(
            get_unapplied_change_permission(self.wip_document, change_number=1)
        )
        self.assertEqual(permission.status, ChangePermission.STATUS_DRAFT)

    def test_approved_unapplied_is_returned(self):
        permission = self._draft()
        approve_change_permission(self.gip, permission)

        link = get_unapplied_change_permission(
            self.wip_document, change_number=1,
        )

        self.assertIsNotNone(link)
        self.assertEqual(link.permission_id, permission.pk)
        self.assertEqual(link.change_number, 1)

    def test_other_change_number_is_not_returned(self):
        permission = self._draft()
        approve_change_permission(self.gip, permission)

        self.assertIsNone(
            get_unapplied_change_permission(
                self.wip_document, change_number=2,
            )
        )

    def test_applied_change_is_not_returned(self):
        permission = self._draft()
        approve_change_permission(self.gip, permission)

        mark_change_permission_applied(permission, self.wip_document)

        self.assertIsNone(
            get_unapplied_change_permission(self.wip_document)
        )
        self.assertIsNone(
            get_unapplied_change_permission(
                self.wip_document, change_number=1,
            )
        )

    def test_repeated_application_is_rejected(self):
        permission = self._draft()
        approve_change_permission(self.gip, permission)
        mark_change_permission_applied(permission, self.wip_document)

        with self.assertRaises(ValidationError):
            mark_change_permission_applied(
                permission, self.wip_document,
            )
