"""SPEC 2.0, волна 4, шаг 3 (W4-3): печать 9/9а, форма 10, аннулирование.

Проверяем формат обозначения «N-YY», контракт печатной формы (дословные
наименования граф, сокращение базовой части), матрицу прав аннулирования,
историю статусов и таблицу регистрации изменений. Локальные прогоны не
запускаются — проверка в CI.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
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
    abbreviate_document_codes,
    add_change_permission_document,
    annul_change_permission,
    approve_change_permission,
    can_annul_change_permission,
    create_change_permission_draft,
    create_project,
    get_change_permission_print_rows,
    get_document_change_registration_rows,
    get_unapplied_change_permission,
    update_change_permission_draft,
    upload_document,
)

User = get_user_model()


class RviPrintTestBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='РВИ-печать')
        self.admin = self._user('print-admin', 'admin')
        self.gip = self._user('print-gip', 'gip')
        self.manager = self._user('print-manager', 'manager')
        self.outsider = self._user('print-outsider', 'developer')
        self.viewer = self._user('print-viewer', 'viewer')
        self.project = create_project(
            company=self.company,
            code='RVI-PRINT',
            name='Объект строительства',
            gip=self.gip,
            created_by=self.admin,
            base_code='2345-12',
        )
        self.section = Section.objects.create(
            project=self.project,
            code='АР',
            name='Архитектурные решения',
            order=1,
        )

    def _user(self, username, role):
        user = User.objects.create_user(username=username, password='pass')
        UserProfile.objects.update_or_create(
            user=user,
            defaults={'role': role, 'company': self.company},
        )
        return user

    def _document(self, code, state=Document.STATE_WIP_DRAFT):
        return Document.objects.create(
            project=self.project,
            section=self.section,
            code=code,
            name=f'Документ {code}',
            doc_type='drawing',
            state=state,
            created_by=self.admin,
        )

    def _draft(self, document, content='Изменить документ', sheets=''):
        permission = create_change_permission_draft(
            self.gip,
            self.project,
            content=content,
            reason_code='4',
            note='Примечание',
        )
        add_change_permission_document(
            self.gip, permission, document, sheets=sheets,
        )
        return permission

    def _approved(self, document, approver=None, sheets=''):
        permission = self._draft(document, sheets=sheets)
        approve_change_permission(approver or self.gip, permission)
        permission.refresh_from_db()
        return permission


class RviCodeFormatTests(RviPrintTestBase):
    def test_code_is_sequence_with_two_digit_year(self):
        permission = self._approved(self._document('2345-12-АР'))

        suffix = timezone.localdate().year % 100
        self.assertEqual(permission.code, f'1-{suffix:02d}')
        self.assertRegex(permission.code, r'^\d+-\d{2}$')

    def test_journal_keeps_sequence_after_annulment(self):
        first = self._approved(self._document('2345-12-АР'))
        annul_change_permission(self.manager, first)

        second = self._approved(self._document('2345-12-КЖ'))
        suffix = timezone.localdate().year % 100
        self.assertEqual(first.code, f'1-{suffix:02d}')
        self.assertEqual(second.code, f'2-{suffix:02d}')


class RviPrintContractTests(RviPrintTestBase):
    def test_abbreviation_of_base_part_for_second_document(self):
        self.assertEqual(
            abbreviate_document_codes(
                ['2345-12-АР', '2345-12-КЖ', '2345-12-ОВ'],
                base_code='2345-12',
            ),
            '2345-12-АР; КЖ; ОВ',
        )
        self.assertEqual(
            abbreviate_document_codes(['АР-01', 'АР-02']),
            'АР-01; 02',
        )

    def test_print_form_contains_verbatim_graph_names(self):
        first = self._document('2345-12-АР')
        second = self._document('2345-12-КЖ')
        permission = create_change_permission_draft(
            self.gip, self.project, content='Изменить планировку',
        )
        add_change_permission_document(
            self.gip, permission, first, sheets='3, 7–9',
        )
        add_change_permission_document(
            self.gip, permission, second, sheets='Все',
        )
        approve_change_permission(self.gip, permission)
        permission.refresh_from_db()

        self.client.force_login(self.gip)
        response = self.client.get(
            reverse(
                'storage:change_permission_print', args=[permission.pk],
            )
        )
        self.assertEqual(response.status_code, 200)
        for label in (
            'Разрешение',
            'Обозначение',
            'Наименование объекта строительства',
            'Изм.',
            'Лист',
            'Содержание изменения',
            'Код',
            'Примечание',
            'Изм. внес',
            'Листов',
            'Составил',
            'ГИП',
            'Утв.',
            'Форма 9',
        ):
            with self.subTest(label=label):
                self.assertContains(response, label)

        self.assertContains(response, '2345-12-АР; КЖ')
        self.assertContains(response, '<u>2345-12-АР</u>')
        self.assertContains(response, '<u>2345-12-КЖ</u>')
        self.assertContains(response, '3, 7–9')
        self.assertContains(response, 'Все')

    def test_print_data_repeats_content_for_each_document(self):
        first = self._document('2345-12-АР')
        second = self._document('2345-12-КЖ')
        permission = self._draft(first)
        add_change_permission_document(self.gip, permission, second)

        data = get_change_permission_print_rows(permission)

        self.assertEqual(len(data['rows']), 2)
        self.assertEqual(
            [row['document_code'] for row in data['rows']],
            ['2345-12-АР', '2345-12-КЖ'],
        )
        self.assertTrue(
            all(row['content'] == permission.content for row in data['rows'])
        )
        self.assertEqual(data['designations'], '2345-12-АР; КЖ')

    def test_print_pdf_export_returns_pdf(self):
        permission = self._approved(self._document('2345-12-АР'))
        self.client.force_login(self.gip)
        response = self.client.get(
            reverse(
                'storage:change_permission_print_pdf', args=[permission.pk],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))


class RviAnnulmentTests(RviPrintTestBase):
    def test_draft_author_gip_can_annul(self):
        permission = self._draft(self._document('2345-12-АР'))

        annul_change_permission(self.gip, permission, comment='Ошибка')

        permission.refresh_from_db()
        self.assertEqual(permission.status, ChangePermission.STATUS_ANNULLED)
        self.assertIsNone(permission.code)
        self.assertTrue(
            permission.status_logs.filter(
                new_status=ChangePermission.STATUS_ANNULLED,
                changed_by=self.gip,
            ).exists()
        )

    def test_manager_can_annul_draft(self):
        permission = self._draft(self._document('2345-12-АР'))

        self.assertTrue(can_annul_change_permission(self.manager, permission))
        annul_change_permission(self.manager, permission)

        permission.refresh_from_db()
        self.assertEqual(permission.status, ChangePermission.STATUS_ANNULLED)

    def test_outsider_cannot_annul_draft(self):
        permission = self._draft(self._document('2345-12-АР'))

        self.assertFalse(
            can_annul_change_permission(self.outsider, permission)
        )
        with self.assertRaises(PermissionDenied):
            annul_change_permission(self.outsider, permission)

        permission.refresh_from_db()
        self.assertEqual(permission.status, ChangePermission.STATUS_DRAFT)

    def test_approved_without_archive_can_be_annulled_by_gip(self):
        permission = self._approved(
            self._document('2345-12-АР', Document.STATE_SHARED),
        )

        self.assertTrue(can_annul_change_permission(self.gip, permission))
        annul_change_permission(self.gip, permission)

        permission.refresh_from_db()
        self.assertEqual(permission.status, ChangePermission.STATUS_ANNULLED)
        self.assertIsNotNone(permission.code)

    def test_approved_with_archive_requires_manager(self):
        permission = self._approved(
            self._document('2345-12-АР', Document.STATE_ARCHIVED),
            approver=self.manager,
        )

        self.assertFalse(can_annul_change_permission(self.gip, permission))
        with self.assertRaises(PermissionDenied):
            annul_change_permission(self.gip, permission)
        self.assertTrue(can_annul_change_permission(self.manager, permission))
        annul_change_permission(self.manager, permission)

        permission.refresh_from_db()
        self.assertEqual(permission.status, ChangePermission.STATUS_ANNULLED)

    def test_annulled_is_view_only_and_not_returned_as_unapplied(self):
        document = self._document('2345-12-АР', Document.STATE_SHARED)
        permission = self._approved(document)
        annul_change_permission(self.manager, permission)
        permission.refresh_from_db()

        self.assertIsNone(get_unapplied_change_permission(document))
        self.assertFalse(can_annul_change_permission(self.manager, permission))
        with self.assertRaises(ValidationError):
            update_change_permission_draft(
                self.gip, permission, content='Подмена',
            )

    def test_annul_endpoint_posts_with_confirmation(self):
        permission = self._draft(self._document('2345-12-АР'))
        self.client.force_login(self.gip)

        response = self.client.post(
            reverse(
                'storage:change_permission_annul', args=[permission.pk],
            ),
            {'comment': 'Аннулировать по ошибке'},
        )

        self.assertEqual(response.status_code, 302)
        permission.refresh_from_db()
        self.assertEqual(permission.status, ChangePermission.STATUS_ANNULLED)
        self.assertTrue(
            permission.status_logs.filter(
                comment='Аннулировать по ошибке',
            ).exists()
        )

    def test_annul_button_not_shown_after_annulment(self):
        permission = self._draft(self._document('2345-12-АР'))
        annul_change_permission(self.gip, permission)
        self.client.force_login(self.gip)

        response = self.client.get(
            reverse(
                'storage:change_permission_detail', args=[permission.pk],
            )
        )

        self.assertNotContains(response, 'data-role="rvi-annul"')
        self.assertContains(response, 'data-role="rvi-annulled-note"')
        self.assertNotContains(response, 'data-role="rvi-edit"')


class DocumentChangeTableTests(RviPrintTestBase):
    def test_version_consumption_marks_applied_action(self):
        document = self._document('2345-12-АР', Document.STATE_SHARED)
        permission = self._approved(document)

        version = upload_document(
            user=self.gip,
            project=self.project,
            section=self.section,
            code=document.code,
            name=document.name,
            doc_type='drawing',
            file=SimpleUploadedFile('v2.pdf', b'pdf-v2'),
            reason_text='Изменение по РВИ',
            change_permission=permission,
        )

        link = ChangePermissionDocument.objects.get(
            permission=permission, document=document,
        )
        self.assertEqual(version.change_number, 1)
        self.assertEqual(link.applied_action, 'version')
        self.assertEqual(link.get_applied_action_display(), 'Загрузка версии')
        self.assertIsNotNone(link.applied_at)

    def test_form_10_row_per_version_released_by_rvi(self):
        document = self._document('2345-12-АР', Document.STATE_SHARED)
        first_permission = self._approved(document, sheets='3')
        upload_document(
            user=self.gip,
            project=self.project,
            section=self.section,
            code=document.code,
            name=document.name,
            doc_type='drawing',
            file=SimpleUploadedFile('v2.pdf', b'pdf-v2'),
            reason_text='Изменение по РВИ',
            change_permission=first_permission,
        )
        second_permission = self._approved(document, sheets='4')
        upload_document(
            user=self.gip,
            project=self.project,
            section=self.section,
            code=document.code,
            name=document.name,
            doc_type='drawing',
            file=SimpleUploadedFile('v3.pdf', b'pdf-v3'),
            reason_text='Второе изменение по РВИ',
            change_permission=second_permission,
        )

        rows = get_document_change_registration_rows(document)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [row['change_number'] for row in rows],
            [1, 2],
        )
        self.assertEqual(
            [row['permission_code'] for row in rows],
            [first_permission.code, second_permission.code],
        )

        self.client.force_login(self.gip)
        response = self.client.get(
            reverse(
                'storage:document_changes_table', args=[document.pk],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Таблица регистрации изменений')
        for label in (
            'Изм.',
            'Номер док.',
            'Дата',
            'Листы изменённые',
            'Листы заменённые',
            'Листы новые',
            'Листы аннулированные',
            'Всего листов в док.',
        ):
            with self.subTest(label=label):
                self.assertContains(response, label)
        self.assertContains(response, first_permission.code)
        self.assertContains(response, second_permission.code)

    def test_form_10_pdf_export_returns_pdf(self):
        document = self._document('2345-12-АР', Document.STATE_SHARED)
        self._approved(document)
        self.client.force_login(self.gip)

        response = self.client.get(
            reverse(
                'storage:document_changes_table_pdf', args=[document.pk],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))