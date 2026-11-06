"""Аудит 6.0: регрессионные тесты на найденные при аудите проблемы.

1. Сигнал UserProfile: новые пользователи без автопривязки к компании;
   профиль создаётся без пиннинга обратного кэша user.userprofile.
2. add_company_member: хардендинг «админ только своей компании».
3. Share-ссылка: скачивание только текущей версии документа.
4. Management-команда backfill_profiles для старых пользователей.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from storage.domain.models import Company, Project, Section, UserProfile
from storage.domain.services import (
    _is_company_admin,
    add_company_member,
    create_project,
    upload_document,
)

User = get_user_model()


class SignalNoAutoAttachTests(TestCase):
    """Сигнал создаёт профиль без компании и без пиннинга кэша."""

    def test_new_user_profile_without_company(self):
        Company.objects.create(name='Первая')
        user = User.objects.create_user('plain', password='pass')
        profile = UserProfile.objects.get(user=user)
        self.assertIsNone(profile.company_id)
        self.assertEqual(profile.role, 'viewer')

    def test_profile_cache_not_pinned_by_signal(self):
        # Регрессия Аудита 6.0: профиль, созданный сигналом через
        # UserProfile(user=instance), пиннился в кэш user.userprofile,
        # и последующие update_or_create видели устаревшие роль/компанию.
        company = Company.objects.create(name='Альфа')
        admin = User.objects.create_user('boss', password='pass')
        UserProfile.objects.update_or_create(
            user=admin, defaults={'role': 'admin', 'company': company},
        )[0]
        # Никакого refresh: первый доступ к userprofile должен вернуть
        # актуальную строку БД.
        self.assertTrue(_is_company_admin(admin))
        self.assertEqual(admin.userprofile.role, 'admin')
        self.assertEqual(admin.userprofile.company_id, company.pk)

    def test_add_member_after_signal(self):
        # Панель управления: админ добавляет нового пользователя —
        # сигнал не должен был заранее привязать его к чужой компании.
        company = Company.objects.create(name='Альфа')
        admin = User.objects.create_user('boss', password='pass')
        UserProfile.objects.update_or_create(
            user=admin, defaults={'role': 'admin', 'company': company},
        )[0]
        newcomer = User.objects.create_user('newbie', password='pass')
        profile = add_company_member(admin, company, newcomer, 'developer')
        self.assertEqual(profile.company_id, company.pk)
        self.assertEqual(profile.role, 'developer')


class CompanyScopeHardeningTests(TestCase):
    """add_company_member: админ чужой компании не проходит."""

    def setUp(self):
        self.company_a = Company.objects.create(name='Альфа')
        self.company_b = Company.objects.create(name='Бета')
        self.admin_a = User.objects.create_user('admin_a', password='pass')
        UserProfile.objects.update_or_create(
            user=self.admin_a,
            defaults={'role': 'admin', 'company': self.company_a},
        )[0]

    def test_admin_of_other_company_denied(self):
        target = User.objects.create_user('target', password='pass')
        with self.assertRaises(PermissionDenied):
            add_company_member(
                self.admin_a, self.company_b, target, 'developer',
            )

    def test_admin_without_company_denied(self):
        no_company = User.objects.create_user('noc', password='pass')
        UserProfile.objects.update_or_create(
            user=no_company, defaults={'role': 'admin'},
        )[0]
        target = User.objects.create_user('target2', password='pass')
        with self.assertRaises(PermissionDenied):
            add_company_member(
                no_company, self.company_a, target, 'developer',
            )


class ShareVersionScopeTests(TestCase):
    """Share-токен даёт файл только текущей версии документа."""

    def setUp(self):
        import uuid
        from datetime import timedelta

        from django.utils import timezone

        self.company = Company.objects.create(name='Альфа')
        self.gip = User.objects.create_user('gip', password='pass')
        UserProfile.objects.update_or_create(
            user=self.gip, defaults={'role': 'gip', 'company': self.company},
        )[0]
        self.admin = User.objects.create_user('boss', password='pass')
        UserProfile.objects.update_or_create(
            user=self.admin,
            defaults={'role': 'admin', 'company': self.company},
        )[0]
        self.project = create_project(
            company=self.company,
            code='SHR-1',
            name='Share',
            customer='',
            gip=self.gip,
            created_by=self.admin,
        )
        self.section = Section.objects.create(
            project=self.project, code='АР', name='АР', order=1,
        )
        buffer = SimpleUploadedFile('v1.pdf', b'pdf-data-v1')
        version1 = upload_document(
            user=self.gip,
            project=self.project,
            section=self.section,
            code='DOC-1',
            name='Документ',
            doc_type='drawing',
            file=buffer,
        )
        self.version1 = version1
        self.document = version1.document
        buffer2 = SimpleUploadedFile('v2.pdf', b'pdf-data-v2')
        self.version2 = upload_document(
            user=self.gip,
            project=self.project,
            section=self.section,
            code='DOC-1',
            name='Документ',
            doc_type='drawing',
            file=buffer2,
        )
        # version1.document — снимок до второй загрузки: актуализируем.
        self.document.refresh_from_db()
        self.document.public_link_token = uuid.uuid4()
        self.document.public_link_expires_at = (
            timezone.now() + timedelta(days=1)
        )
        self.document.save(update_fields=[
            'public_link_token', 'public_link_expires_at',
        ])
        self.token = str(self.document.public_link_token)

    def test_current_version_downloads_by_share(self):
        response = self.client.get(
            reverse('storage:document_download', args=[self.version2.pk]),
            {'share': self.token},
        )
        self.assertEqual(response.status_code, 200)

    def test_old_version_hidden_from_share(self):
        # Регрессия Аудит 6.0: раньше по ссылке отдавалась ЛЮБАЯ версия.
        self.assertNotEqual(
            self.document.current_version_id, self.version1.pk,
        )
        response = self.client.get(
            reverse('storage:document_download', args=[self.version1.pk]),
            {'share': self.token},
        )
        self.assertEqual(response.status_code, 404)


class BackfillProfilesTests(TestCase):
    """Команда backfill_profiles: профили и привязка старых пользователей."""

    def test_backfill_creates_profiles_and_attaches(self):
        company = Company.objects.create(name='Альфа')
        legacy = User.objects.create_user('abel', password='pass')
        # Симуляция старого пользователя: профиль удалён (сигнал его создал).
        UserProfile.objects.filter(user=legacy).delete()

        call_command('backfill_profiles', '--company', str(company.pk))

        profile = UserProfile.objects.get(user=legacy)
        self.assertEqual(profile.role, 'viewer')
        self.assertEqual(profile.company_id, company.pk)

    def test_backfill_is_idempotent(self):
        company = Company.objects.create(name='Альфа')
        admin = User.objects.create_user('boss', password='pass')
        UserProfile.objects.update_or_create(
            user=admin,
            defaults={'role': 'admin', 'company': company},
        )[0]

        call_command('backfill_profiles', '--company', str(company.pk))

        admin.userprofile.refresh_from_db()
        # Роль и компания не перезаписаны.
        self.assertEqual(admin.userprofile.role, 'admin')
        self.assertEqual(admin.userprofile.company_id, company.pk)

    def test_backfill_dry_run_changes_nothing(self):
        company = Company.objects.create(name='Альфа')
        legacy = User.objects.create_user('legacy', password='pass')
        UserProfile.objects.filter(user=legacy).delete()

        call_command('backfill_profiles', '--company', str(company.pk),
                     '--dry-run')

        self.assertFalse(
            UserProfile.objects.filter(user=legacy).exists(),
        )
