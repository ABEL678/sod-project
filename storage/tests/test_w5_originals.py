"""W5-1: подлинники РВИ (архив организации).

Проверяются точки записи (утверждение/аннулирование), неизменяемость
(insert-only), сквозной инвентарный номер, SHA-256 файла, дублирование
хэша и номера в статус-логе, сверка целостности и сохранность печати 9/9а.
"""

import hashlib
import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from storage.domain.models import (
    ChangePermission,
    ChangePermissionOriginal,
    ChangePermissionOriginalRegistry,
    ChangePermissionStatusLog,
    Company,
    Document,
    Section,
    UserProfile,
)
from storage.domain.services import (
    approve_change_permission,
    annul_change_permission,
    create_change_permission_draft,
    create_project,
    get_change_permission_print_rows,
)

User = get_user_model()


class ChangePermissionOriginalsTestBase(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        from django.test.utils import override_settings

        self._media_override = override_settings(MEDIA_ROOT=self.media_root)
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)
        self.company = Company.objects.create(name='Подлинники Организация')
        self.admin = User.objects.create_user(
            username='orig_admin', password='pass123',
        )
        UserProfile.objects.update_or_create(
            user=self.admin,
            defaults={'role': 'admin', 'company': self.company},
        )
        self.gip = User.objects.create_user(
            username='orig_gip', password='pass123',
        )
        UserProfile.objects.update_or_create(
            user=self.gip,
            defaults={'role': 'gip', 'company': self.company},
        )
        self.manager = User.objects.create_user(
            username='orig_manager', password='pass123',
        )
        UserProfile.objects.update_or_create(
            user=self.manager,
            defaults={'role': 'manager', 'company': self.company},
        )
        self.project = create_project(
            company=self.company,
            code='ORIG-001',
            name='Объект подлинников',
            gip=self.gip,
            created_by=self.admin,
        )
        self.section = Section.objects.create(
            project=self.project,
            code='АР',
            name='Архитектурные решения',
            order=1,
        )
        self.document = Document.objects.create(
            project=self.project,
            section=self.section,
            code='ORIG-DOC',
            name='Документ подлинника',
            doc_type='drawing',
            state=Document.STATE_WIP_DRAFT,
            created_by=self.admin,
        )

    def tearDown(self):
        import shutil

        self._media_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)
        super().tearDown()

    def _draft(self, content='Изменить планировку'):
        permission = create_change_permission_draft(
            self.gip,
            self.project,
            content=content,
            reason_code='1',
            note='Примечание',
        )
        return permission


class ApproveCreatesOriginalTests(ChangePermissionOriginalsTestBase):
    def test_approve_creates_original_with_sha256_and_inventory(self):
        permission = self._draft()
        with self.settings(MEDIA_ROOT=self.media_root):
            approved = approve_change_permission(self.manager, permission)

        original = ChangePermissionOriginal.objects.get(
            permission=approved,
            record_type=ChangePermissionOriginal.TYPE_APPROVAL,
        )
        self.assertEqual(original.sha256, hashlib.sha256(
            original.file.read()).hexdigest())
        self.assertTrue(original.inventory_number)
        self.assertEqual(
            original.inventory_number.split('-')[-1],
            str(timezone.localdate().year % 100).zfill(2),
        )
        self.assertEqual(
            original.recorded_at.date(),
            timezone.localdate(),
        )

    def test_approve_duplicates_hash_and_inventory_in_status_log(self):
        permission = self._draft()
        with self.settings(MEDIA_ROOT=self.media_root):
            approved = approve_change_permission(self.manager, permission)

        original = ChangePermissionOriginal.objects.get(
            permission=approved,
            record_type=ChangePermissionOriginal.TYPE_APPROVAL,
        )
        log = ChangePermissionStatusLog.objects.filter(
            permission=approved,
            new_status=ChangePermission.STATUS_APPROVED,
        ).order_by('-created_at').first()
        self.assertIsNotNone(log)
        self.assertIn(original.sha256[:12], log.comment)
        self.assertIn(original.inventory_number, log.comment)


class AnnulCreatesOriginalTests(ChangePermissionOriginalsTestBase):
    def test_annul_creates_original_with_annulment_mark(self):
        permission = self._draft()
        with self.settings(MEDIA_ROOT=self.media_root):
            approved = approve_change_permission(self.manager, permission)
            annul_change_permission(self.manager, approved, comment='Отмена')

        original = ChangePermissionOriginal.objects.get(
            permission=approved,
            record_type=ChangePermissionOriginal.TYPE_ANNULMENT,
        )
        self.assertTrue(original.file.read())
        self.assertEqual(original.sha256, hashlib.sha256(
            original.file.read()).hexdigest())
        self.assertTrue(original.inventory_number)

        rows = get_change_permission_print_rows(approved)
        self.assertTrue(any(
            'АННУЛИРОВАНО' in (row.get('note') or '')
            for row in rows['rows']
        ))


class OriginalImmutabilityTests(ChangePermissionOriginalsTestBase):
    def test_repeated_approve_raises(self):
        permission = self._draft()
        with self.settings(MEDIA_ROOT=self.media_root):
            approved = approve_change_permission(self.manager, permission)
            with self.assertRaises(ValidationError):
                approve_change_permission(self.manager, approved)

    def test_update_raises(self):
        permission = self._draft()
        with self.settings(MEDIA_ROOT=self.media_root):
            approved = approve_change_permission(self.manager, permission)

        original = ChangePermissionOriginal.objects.get(
            permission=approved,
            record_type=ChangePermissionOriginal.TYPE_APPROVAL,
        )
        original.sha256 = '0' * 64
        with self.assertRaises(ValidationError):
            original.save()

    def test_delete_raises(self):
        permission = self._draft()
        with self.settings(MEDIA_ROOT=self.media_root):
            approved = approve_change_permission(self.manager, permission)

        original = ChangePermissionOriginal.objects.get(
            permission=approved,
            record_type=ChangePermissionOriginal.TYPE_APPROVAL,
        )
        with self.assertRaises(ValidationError):
            original.delete()


class HashVerificationTests(ChangePermissionOriginalsTestBase):
    def test_verify_sha256_ok(self):
        permission = self._draft()
        with self.settings(MEDIA_ROOT=self.media_root):
            approved = approve_change_permission(self.manager, permission)

        original = ChangePermissionOriginal.objects.get(
            permission=approved,
            record_type=ChangePermissionOriginal.TYPE_APPROVAL,
        )
        content = original.file.read()
        self.assertTrue(original.verify_sha256(content))

    def test_verify_sha256_fails_after_tamper(self):
        permission = self._draft()
        with self.settings(MEDIA_ROOT=self.media_root):
            approved = approve_change_permission(self.manager, permission)

        original = ChangePermissionOriginal.objects.get(
            permission=approved,
            record_type=ChangePermissionOriginal.TYPE_APPROVAL,
        )
        self.assertFalse(original.verify_sha256(b'tampered-content'))


class InventoryRegistryTests(ChangePermissionOriginalsTestBase):
    def test_inventory_number_is_sequential_per_organization(self):
        first = create_change_permission_draft(
            self.gip, self.project, content='Первое', reason_code='1',
        )
        second = create_change_permission_draft(
            self.gip, self.project, content='Второе', reason_code='1',
        )
        with self.settings(MEDIA_ROOT=self.media_root):
            approve_change_permission(self.manager, first)
            approve_change_permission(self.manager, second)

        numbers = ChangePermissionOriginal.objects.filter(
            permission__in=[first, second],
            record_type=ChangePermissionOriginal.TYPE_APPROVAL,
        ).order_by('recorded_at').values_list('inventory_number', flat=True)
        self.assertEqual(len(numbers), 2)
        first_num = int(numbers[0].split('-')[0])
        second_num = int(numbers[1].split('-')[0])
        self.assertEqual(second_num, first_num + 1)

    def test_registry_row_created(self):
        self.assertFalse(
            ChangePermissionOriginalRegistry.objects.filter(
                organization=self.company,
            ).exists()
        )
        permission = self._draft()
        with self.settings(MEDIA_ROOT=self.media_root):
            approve_change_permission(self.manager, permission)

        self.assertTrue(
            ChangePermissionOriginalRegistry.objects.filter(
                organization=self.company,
            ).exists()
        )


class Print9IntegrityTests(ChangePermissionOriginalsTestBase):
    def test_print_rows_structure_unchanged(self):
        permission = self._draft()
        rows = get_change_permission_print_rows(permission)
        self.assertIn('code', rows)
        self.assertIn('rows', rows)
        self.assertIn('designations', rows)
        self.assertIn('approved_at', rows)
        self.assertTrue(rows['rows'])
        self.assertEqual(
            set(rows['rows'][0].keys()),
            {
                'document_code', 'document_name', 'change_number', 'sheets',
                'content', 'reason_code', 'note',
            },
        )