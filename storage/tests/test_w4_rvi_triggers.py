"""SPEC 2.0, волна 4, шаг 2 (W4-2): точки срабатывания РВИ и UI.

Проверяем сервисные триггеры (загрузка версии, вывод из архива,
аннулирование), автоназначение номера изменения и раздел «РВИ» в UI.
Полные прогоны не запускаются — локально только этот модуль.
"""

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from issues.models import Issue
from storage.domain.models import (
    CHANGE_PERMISSION_REASON_CHOICES,
    ChangePermission,
    ChangePermissionDocument,
    Document,
    UserProfile,
)
from storage.domain.services import (
    add_change_permission_document,
    annul_document,
    approve_change_permission,
    can_approve_change_permission,
    create_change_permission_draft,
    create_folder,
    create_project,
    transition_document_zone,
    upload_document,
)
from django.contrib.auth import get_user_model

User = get_user_model()


class RviTestBase(TestCase):
    def setUp(self):
        from storage.domain.models import Company, Section

        self.company = Company.objects.create(name='РВИ-компания')
        self.admin = User.objects.create_user('rvi-admin', password='pass')
        UserProfile.objects.update_or_create(
            user=self.admin,
            defaults={'role': 'admin', 'company': self.company},
        )
        self.gip = User.objects.create_user('rvi-gip', password='pass')
        UserProfile.objects.update_or_create(
            user=self.gip,
            defaults={'role': 'gip', 'company': self.company},
        )
        self.manager = User.objects.create_user('rvi-manager', password='pass')
        UserProfile.objects.update_or_create(
            user=self.manager,
            defaults={'role': 'manager', 'company': self.company},
        )
        self.viewer = User.objects.create_user('rvi-viewer', password='pass')
        UserProfile.objects.update_or_create(
            user=self.viewer,
            defaults={'role': 'viewer', 'company': self.company},
        )
        self.project = create_project(
            company=self.company,
            code='RVI-01',
            name='Проект РВИ',
            customer_id=None,
            gip=self.gip,
            created_by=self.admin,
        )
        self.section = Section.objects.create(
            project=self.project, code='АР', name='АР', order=1,
        )

    def _upload(self, code='RVI-DOC', folder=None, user=None, **kwargs):
        return upload_document(
            user=user or self.gip,
            project=self.project,
            section=self.section,
            code=code,
            name=f'Документ {code}',
            doc_type='drawing',
            file=SimpleUploadedFile(f'{code}.pdf', b'pdf-data'),
            folder=folder,
            reason_text='Тестовая причина',
            **kwargs,
        ).document

    def _make_ready(self, document):
        document.state = Document.STATE_WIP_READY
        document._from_service = True
        document.save(update_fields=['state'])
        return document

    def _shared(self, code='RVI-SHARED', outcome='A'):
        document = self._upload(code=code)
        if outcome == 'B':
            Issue.objects.create(
                document=document,
                author=self.gip,
                description='Неблокирующее замечание',
                status='DISCUSSION',
                is_blocking=False,
            )
        self._make_ready(document)
        transition_document_zone(self.admin, document, 'submit_review')
        document.refresh_from_db()
        return document

    def _published(self, code='RVI-PUB'):
        document = self._shared(code=code)
        transition_document_zone(self.admin, document, 'publish')
        document.refresh_from_db()
        return document

    def _archived(self, code='RVI-ARCH', folder=None):
        document = self._upload(code=code, folder=folder)
        self._make_ready(document)
        transition_document_zone(self.admin, document, 'submit_review')
        transition_document_zone(self.admin, document, 'publish')
        transition_document_zone(self.admin, document, 'archive')
        document.refresh_from_db()
        return document

    def _draft(self, document, content='Изменить документ'):
        permission = create_change_permission_draft(
            self.gip, self.project, content=content, reason_code='1',
        )
        add_change_permission_document(self.gip, permission, document)
        return permission

    def _approved(self, document, approver=None):
        permission = self._draft(document)
        approve_change_permission(approver or self.gip, permission)
        permission.refresh_from_db()
        return permission


class UploadVersionTriggerTests(RviTestBase):
    def test_wip_upload_does_not_require_change_permission(self):
        document = self._upload(code='RVI-WIP')
        version = upload_document(
            user=self.gip,
            project=self.project,
            section=self.section,
            code=document.code,
            name=document.name,
            doc_type='drawing',
            file=SimpleUploadedFile('v2.pdf', b'pdf-v2'),
            reason_text='Свободная правка WIP',
        )
        self.assertEqual(version.version_number, 2)
        self.assertIsNone(version.change_permission_id)
        self.assertIsNone(version.change_number)

    def test_shared_upload_requires_approved_permission(self):
        for outcome in ('A', 'B'):
            with self.subTest(outcome=outcome):
                document = self._shared(
                    code=f'RVI-SHARED-{outcome}', outcome=outcome,
                )
                with self.assertRaises(PermissionDenied) as ctx:
                    upload_document(
                        user=self.gip,
                        project=self.project,
                        section=self.section,
                        code=document.code,
                        name=document.name,
                        doc_type='drawing',
                        file=SimpleUploadedFile('v2.pdf', b'pdf-v2'),
                        reason_text='Без основания',
                    )
                self.assertIn('РВИ', str(ctx.exception))

    def test_shared_upload_with_draft_permission_is_rejected(self):
        document = self._shared(code='RVI-SHARED-DRAFT')
        permission = self._draft(document)
        with self.assertRaises(PermissionDenied):
            upload_document(
                user=self.gip,
                project=self.project,
                section=self.section,
                code=document.code,
                name=document.name,
                doc_type='drawing',
                file=SimpleUploadedFile('v2.pdf', b'pdf-v2'),
                reason_text='Черновик не основание',
                change_permission=permission,
            )

    def test_shared_upload_consumes_change_and_sets_number(self):
        document = self._shared(code='RVI-SHARED-OK')
        permission = self._approved(document)

        version = upload_document(
            user=self.gip,
            project=self.project,
            section=self.section,
            code=document.code,
            name=document.name,
            doc_type='drawing',
            file=SimpleUploadedFile('v2.pdf', b'pdf-v2'),
            reason_text='По утверждённому РВИ',
            change_permission=permission,
        )

        link = ChangePermissionDocument.objects.get(
            permission=permission, document=document,
        )
        self.assertEqual(version.change_number, 1)
        self.assertEqual(version.change_permission, permission)
        self.assertIsNotNone(link.applied_at)
        self.assertEqual(
            link.applied_action,
            ChangePermissionDocument.APPLIED_BY_VERSION,
        )

    def test_repeat_use_of_one_change_is_rejected(self):
        document = self._shared(code='RVI-SHARED-ONCE')
        permission = self._approved(document)
        upload_document(
            user=self.gip,
            project=self.project,
            section=self.section,
            code=document.code,
            name=document.name,
            doc_type='drawing',
            file=SimpleUploadedFile('v2.pdf', b'pdf-v2'),
            reason_text='Первая версия',
            change_permission=permission,
        )
        with self.assertRaises(PermissionDenied):
            upload_document(
                user=self.gip,
                project=self.project,
                section=self.section,
                code=document.code,
                name=document.name,
                doc_type='drawing',
                file=SimpleUploadedFile('v3.pdf', b'pdf-v3'),
                reason_text='Повторное использование',
                change_permission=permission,
            )

    def test_published_upload_requires_permission_and_succeeds_with_it(self):
        document = self._published(code='RVI-PUB')
        with self.assertRaises(PermissionDenied):
            upload_document(
                user=self.admin,
                project=self.project,
                section=self.section,
                code=document.code,
                name=document.name,
                doc_type='drawing',
                file=SimpleUploadedFile('v2.pdf', b'pdf-v2'),
                reason_text='Без РВИ',
            )
        permission = self._approved(document)
        version = upload_document(
            user=self.admin,
            project=self.project,
            section=self.section,
            code=document.code,
            name=document.name,
            doc_type='drawing',
            file=SimpleUploadedFile('v2.pdf', b'pdf-v2'),
            reason_text='По РВИ',
            change_permission=permission,
        )
        self.assertEqual(version.change_number, 1)
        self.assertEqual(version.change_permission_id, permission.pk)

    def test_archive_upload_requires_permission_and_succeeds_with_it(self):
        document = self._archived(code='RVI-ARCH')
        with self.assertRaises(PermissionDenied):
            upload_document(
                user=self.admin,
                project=self.project,
                section=self.section,
                code=document.code,
                name=document.name,
                doc_type='drawing',
                file=SimpleUploadedFile('v2.pdf', b'pdf-v2'),
                reason_text='Без РВИ',
            )
        permission = self._approved(document, approver=self.manager)
        version = upload_document(
            user=self.admin,
            project=self.project,
            section=self.section,
            code=document.code,
            name=document.name,
            doc_type='drawing',
            file=SimpleUploadedFile('v2.pdf', b'pdf-v2'),
            reason_text='По РВИ',
            change_permission=permission,
        )
        self.assertEqual(version.change_number, 1)
        self.assertEqual(version.change_permission_id, permission.pk)


class UnarchiveTriggerTests(RviTestBase):
    def _archived_in_custom_folder(self):
        folder = create_folder(
            self.admin, self.project, 'Рабочая папка', section=self.section,
        )
        document = self._archived(code='RVI-UNARCH', folder=folder)
        self.assertEqual(document.folder_id, folder.pk)
        return document, folder

    def test_unarchive_without_permission_is_rejected(self):
        document, _ = self._archived_in_custom_folder()
        with self.assertRaises(PermissionDenied):
            transition_document_zone(self.gip, document, 'unarchive')
        document.refresh_from_db()
        self.assertEqual(document.state, Document.STATE_ARCHIVED)

    def test_unarchive_with_permission_keeps_placement_and_consumes(self):
        document, folder = self._archived_in_custom_folder()
        permission = self._approved(document, approver=self.manager)

        transition_document_zone(
            self.gip, document, 'unarchive', change_permission=permission,
        )

        document.refresh_from_db()
        link = ChangePermissionDocument.objects.get(
            permission=permission, document=document,
        )
        self.assertEqual(document.state, Document.STATE_WIP_DRAFT)
        self.assertEqual(document.status, 'draft')
        self.assertEqual(document.folder_id, folder.pk)
        self.assertIsNotNone(link.applied_at)
        self.assertEqual(
            link.applied_action,
            ChangePermissionDocument.APPLIED_BY_UNARCHIVE,
        )

    def test_repeated_unarchive_with_same_permission_is_rejected(self):
        document, _ = self._archived_in_custom_folder()
        permission = self._approved(document, approver=self.manager)
        transition_document_zone(
            self.gip, document, 'unarchive', change_permission=permission,
        )
        with self.assertRaises((PermissionDenied, ValueError)):
            transition_document_zone(
                self.gip, document, 'unarchive',
                change_permission=permission,
            )

    def test_version_after_unarchive_reuses_permission_without_new_consume(self):
        document, _ = self._archived_in_custom_folder()
        permission = self._approved(document, approver=self.manager)
        transition_document_zone(
            self.gip, document, 'unarchive', change_permission=permission,
        )
        document.refresh_from_db()

        version = upload_document(
            user=self.gip,
            project=self.project,
            section=self.section,
            code=document.code,
            name=document.name,
            doc_type='drawing',
            file=SimpleUploadedFile('v2.pdf', b'pdf-v2'),
            reason_text='Изменение после вывода',
            change_permission=permission,
        )

        link = ChangePermissionDocument.objects.get(
            permission=permission, document=document,
        )
        self.assertEqual(version.change_number, 1)
        self.assertEqual(version.change_permission_id, permission.pk)
        self.assertEqual(
            link.applied_action,
            ChangePermissionDocument.APPLIED_BY_UNARCHIVE,
        )
        self.assertEqual(document.versions.count(), 2)


class AnnulmentTests(RviTestBase):
    def test_annulment_without_permission_is_rejected(self):
        document = self._published(code='RVI-ANN-NO')
        with self.assertRaises(PermissionDenied):
            annul_document(self.gip, document, None)
        document.refresh_from_db()
        self.assertFalse(document.is_annulled)

    def test_annulment_with_permission_consumes_the_change(self):
        document = self._published(code='RVI-ANN-OK')
        permission = self._approved(document)

        annul_document(self.gip, document, permission)

        document.refresh_from_db()
        link = ChangePermissionDocument.objects.get(
            permission=permission, document=document,
        )
        self.assertTrue(document.is_annulled)
        self.assertEqual(document.annulled_by, self.gip)
        self.assertEqual(document.annulment_permission_id, permission.pk)
        self.assertEqual(
            link.applied_action,
            ChangePermissionDocument.APPLIED_BY_ANNULMENT,
        )
        self.assertIsNotNone(link.applied_at)


class ChangeNumberTests(RviTestBase):
    def test_numbers_follow_document_history(self):
        document = self._shared(code='RVI-NUM')
        first = self._approved(document)

        second = create_change_permission_draft(
            self.gip, self.project, content='Второе изменение',
        )
        link = add_change_permission_document(
            self.gip, second, document,
        )
        approve_change_permission(self.gip, second)

        self.assertEqual(
            ChangePermissionDocument.objects.get(
                permission=first, document=document,
            ).change_number,
            1,
        )
        self.assertEqual(link.change_number, 2)

    def test_two_documents_in_one_permission_have_own_numbers(self):
        first_doc = self._upload(code='RVI-NUM-A')
        second_doc = self._upload(code='RVI-NUM-B')
        permission = create_change_permission_draft(
            self.gip, self.project, content='Общее изменение',
        )
        first = add_change_permission_document(
            self.gip, permission, first_doc,
        )
        second = add_change_permission_document(
            self.gip, permission, second_doc,
        )
        self.assertEqual(first.change_number, 1)
        self.assertEqual(second.change_number, 1)


class ChangePermissionUiTests(RviTestBase):
    def _approved_for_ui(self):
        document = self._published(code='RVI-UI')
        return self._approved(document)

    def test_gip_creates_and_sees_draft(self):
        self.client.force_login(self.gip)
        response = self.client.post(
            reverse('storage:change_permission_create'),
            {
                'project': self.project.pk,
                'content': 'Аннулировать документ RVI-UI',
                'reason_code': '4',
                'note': 'Примечание',
            },
        )
        self.assertEqual(response.status_code, 302)
        permission = ChangePermission.objects.get(project=self.project)
        self.assertEqual(permission.status, ChangePermission.STATUS_DRAFT)
        self.assertEqual(permission.reason_code, '4')

        detail = self.client.get(
            reverse(
                'storage:change_permission_detail',
                args=[permission.pk],
            )
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'data-role="rvi-detail"')
        self.assertContains(detail, 'data-role="rvi-documents"')
        self.assertContains(detail, 'data-role="rvi-status-history"')

    def test_viewer_has_no_access_to_rvi_section(self):
        self.client.force_login(self.viewer)
        self.assertEqual(
            self.client.get(reverse('storage:change_permission_list')).status_code,
            403,
        )

    def test_manager_sees_organization_rvis_and_contract_attributes(self):
        permission = self._approved_for_ui()
        self.client.force_login(self.manager)
        response = self.client.get(reverse('storage:change_permission_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-role="rvi-list"')
        self.assertContains(response, 'data-role="rvi-filters"')
        self.assertContains(response, permission.code)

        detail = self.client.get(
            reverse(
                'storage:change_permission_detail',
                args=[permission.pk],
            )
        )
        self.assertContains(detail, 'data-role="rvi-detail"')
        self.assertContains(detail, 'data-role="rvi-status-history"')

    def test_manager_approves_draft_via_ui(self):
        document = self._upload(code='RVI-UI-APPROVE')
        permission = self._draft(document)
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse(
                'storage:change_permission_approve',
                args=[permission.pk],
            )
        )
        self.assertEqual(response.status_code, 302)
        permission.refresh_from_db()
        self.assertEqual(permission.status, ChangePermission.STATUS_APPROVED)
        self.assertIsNotNone(permission.code)
        self.assertEqual(permission.approved_by, self.manager)
        self.assertTrue(
            permission.status_logs.filter(
                new_status=ChangePermission.STATUS_APPROVED,
            ).exists()
        )

    def test_approve_button_visibility_by_matrix(self):
        archive_doc = self._archived(code='RVI-UI-ARCH')
        draft = self._draft(archive_doc)
        detail_url = reverse(
            'storage:change_permission_detail', args=[draft.pk],
        )

        self.client.force_login(self.gip)
        gip_response = self.client.get(detail_url)
        self.assertNotContains(gip_response, 'data-role="rvi-approve"')
        self.assertFalse(can_approve_change_permission(self.gip, draft))

        self.client.force_login(self.manager)
        manager_response = self.client.get(detail_url)
        self.assertContains(manager_response, 'data-role="rvi-approve"')
        self.assertTrue(can_approve_change_permission(self.manager, draft))

    def test_approved_permission_is_read_only_in_ui(self):
        permission = self._approved_for_ui()
        self.client.force_login(self.gip)
        detail_url = reverse(
            'storage:change_permission_detail', args=[permission.pk],
        )
        detail = self.client.get(detail_url)
        self.assertNotContains(detail, 'data-role="rvi-edit"')
        self.assertNotContains(detail, 'data-role="rvi-add-document"')
        self.assertNotContains(detail, 'data-role="rvi-approve"')
        self.assertContains(detail, permission.code)

        edit_url = reverse(
            'storage:change_permission_edit', args=[permission.pk],
        )
        response = self.client.post(
            edit_url, {'content': 'Подмена утверждённого', 'reason_code': '1'},
        )
        self.assertEqual(response.status_code, 403)
        permission.refresh_from_db()
        self.assertNotEqual(permission.content, 'Подмена утверждённого')

        add_url = reverse(
            'storage:change_permission_document_add',
            args=[permission.pk],
        )
        document = self._upload(code='RVI-UI-EXTRA')
        response = self.client.post(
            add_url, {'document_id': document.pk, 'sheets': '1'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(permission.document_links.count(), 1)

        # PUT тоже не должен менять утверждённое РВИ.
        put_response = self.client.put(
            edit_url, {'content': 'PUT-подмена'},
        )
        self.assertIn(put_response.status_code, (403, 405))

    def test_reason_codes_match_gost_table(self):
        self.assertEqual(
            [label for _, label in CHANGE_PERMISSION_REASON_CHOICES],
            [
                'Введение усовершенствований',
                'Изменение стандартов и норм',
                'Дополнительные требования заказчика',
                'Устранение ошибок',
                'Другие причины',
            ],
        )
