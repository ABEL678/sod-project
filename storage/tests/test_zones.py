from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, TransactionTestCase

from storage.domain.models import (
    Company,
    Document,
    Folder,
    Project,
    Section,
    UserProfile,
    User,
)
from storage.domain.services import (
    check_zone_permission,
    create_project,
    create_folder,
    create_section,
    move_document,
    upload_document,
    _ensure_project_zones,
)

User = get_user_model()


def _create_test_data():
    """Создаёт базовые объекты для тестов."""
    company = Company.objects.create(name='Test Corp')
    admin = User.objects.create_user(
        username='admin', password='pass123'
    )
    UserProfile.objects.create(user=admin, role='admin', company=company)
    
    gip = User.objects.create_user(username='gip', password='pass123')
    gip_profile = UserProfile.objects.create(user=gip, role='gip', company=company)
    
    developer = User.objects.create_user(username='eng', password='pass123')
    developer_profile = UserProfile.objects.create(user=developer, role='developer', company=company)
    
    viewer = User.objects.create_user(username='viewer', password='pass123')
    UserProfile.objects.create(user=viewer, role='viewer', company=company)
    
    # Используем create_project для авто-создания зон
    project = create_project(
        company=company,
        code='TEST-001',
        name='Test Project',
        customer='',
        gip=gip,
        created_by=admin,
    )
    section = Section.objects.create(
        project=project, code='АР', name='Архитектурные решения', order=1
    )
    # Добавляем section в allowed_sections инженера
    developer_profile.allowed_sections.add(section)
    
    return {
        'company': company,
        'admin': admin,
        'gip': gip,
        'developer': developer,
        'viewer': viewer,
        'project': project,
        'section': section,
    }


class ZoneFolderCreationTest(TestCase):
    """Тесты создания зон при создании проекта."""

    def setUp(self):
        data = _create_test_data()
        self.admin = data['admin']
        self.project = data['project']

    def test_create_project_creates_zones(self):
        """При создании проекта автоматически создаются 4 зоны."""
        zones = Folder.objects.filter(
            project=self.project, zone__in=dict(Folder.ZONE_CHOICES)
        )
        self.assertEqual(zones.count(), 4)
        zone_codes = set(zones.values_list('zone', flat=True))
        self.assertEqual(zone_codes, {'WIP', 'SHARED', 'PUBLISHED', 'ARCHIVE'})

    def test_zone_names_and_order(self):
        """Зоны создаются с правильными именами и порядком."""
        wip = Folder.objects.get(project=self.project, zone='WIP')
        shared = Folder.objects.get(project=self.project, zone='SHARED')
        published = Folder.objects.get(project=self.project, zone='PUBLISHED')
        archive = Folder.objects.get(project=self.project, zone='ARCHIVE')

        self.assertEqual(wip.name, '01. В работе')
        self.assertEqual(wip.order, 1)
        self.assertEqual(shared.name, '02. На согласовании')
        self.assertEqual(shared.order, 2)
        self.assertEqual(published.name, '03. Опубликовано')
        self.assertEqual(published.order, 3)
        self.assertEqual(archive.name, '04. Архив')
        self.assertEqual(archive.order, 4)

    def test_zones_have_no_section(self):
        """Корневые зоны не привязаны к разделу."""
        zones = Folder.objects.filter(
            project=self.project, zone__in=dict(Folder.ZONE_CHOICES)
        )
        for zone in zones:
            self.assertIsNone(zone.section)
            self.assertIsNone(zone.parent)


class ZoneStatusLinkTest(TestCase):
    """Тесты связи статуса документа и зоны."""

    def setUp(self):
        data = _create_test_data()
        self.admin = data['admin']
        self.project = data['project']
        self.section = data['section']

        # Получаем зоны
        self.wip_folder = Folder.objects.get(project=self.project, zone='WIP')
        self.shared_folder = Folder.objects.get(project=self.project, zone='SHARED')
        self.published_folder = Folder.objects.get(
            project=self.project, zone='PUBLISHED'
        )
        self.archive_folder = Folder.objects.get(project=self.project, zone='ARCHIVE')

    def _create_document(self, folder=None):
        from django.core.files.uploadedfile import SimpleUploadedFile

        file = SimpleUploadedFile(
            'test.pdf', b'%PDF-1.4 test content', content_type='application/pdf'
        )
        upload_document(
            user=self.admin,
            project=self.project,
            section=self.section,
            code='TEST-001',
            name='Test Document',
            doc_type='drawing',
            file=file,
            folder=folder,
        )
        return Document.objects.get(
            project=self.project, section=self.section, code='TEST-001'
        )

    def test_move_to_published_changes_status(self):
        """Перемещение в PUBLISHED меняет статус на approved."""
        doc = self._create_document(self.wip_folder)
        self.assertEqual(doc.status, 'draft')

        move_document(self.admin, doc, self.published_folder)
        doc.refresh_from_db()
        self.assertEqual(doc.status, 'approved')

    def test_move_to_archive_changes_status(self):
        """Перемещение в ARCHIVE меняет статус на archived."""
        doc = self._create_document(self.wip_folder)
        self.assertEqual(doc.status, 'draft')

        move_document(self.admin, doc, self.archive_folder)
        doc.refresh_from_db()
        self.assertEqual(doc.status, 'archived')

    def test_move_to_shared_changes_status(self):
        """Перемещение в SHARED меняет статус на in_review."""
        doc = self._create_document(self.wip_folder)
        self.assertEqual(doc.status, 'draft')

        move_document(self.admin, doc, self.shared_folder)
        doc.refresh_from_db()
        self.assertEqual(doc.status, 'in_review')

    def test_move_to_wip_changes_status(self):
        """Перемещение в WIP меняет статус на draft."""
        # Создаём документ и перемещаем в SHARED (status=in_review)
        doc = self._create_document(self.wip_folder)
        self.assertEqual(doc.status, 'draft')
        move_document(self.admin, doc, self.shared_folder)
        doc.refresh_from_db()
        self.assertEqual(doc.status, 'in_review')
        
        # Теперь перемещаем в WIP (status=draft)
        move_document(self.admin, doc, self.wip_folder)
        doc.refresh_from_db()
        self.assertEqual(doc.status, 'draft')

    def test_move_without_folder_preserves_status(self):
        """Перемещение в None не меняет статус."""
        doc = self._create_document(self.wip_folder)
        original_status = doc.status
        move_document(self.admin, doc, None)
        doc.refresh_from_db()
        self.assertEqual(doc.status, original_status)


class ZonePermissionTest(TestCase):
    """Тесты прав доступа на основе зон."""

    def setUp(self):
        data = _create_test_data()
        self.admin = data['admin']
        self.gip = data['gip']
        self.developer = data['developer']
        self.viewer = data['viewer']
        self.project = data['project']
        self.section = data['section']

        self.wip_folder = Folder.objects.get(project=self.project, zone='WIP')
        self.shared_folder = Folder.objects.get(project=self.project, zone='SHARED')
        self.published_folder = Folder.objects.get(
            project=self.project, zone='PUBLISHED'
        )
        self.archive_folder = Folder.objects.get(project=self.project, zone='ARCHIVE')

    def test_wip_developer_can_write(self):
        """Разработчик может писать в WIP."""
        self.assertTrue(
            check_zone_permission(self.developer, self.wip_folder, self.section)
        )

    def test_wip_gip_can_write(self):
        """ГИП может писать в WIP."""
        self.assertTrue(check_zone_permission(self.gip, self.wip_folder, self.section))

    def test_wip_admin_can_write(self):
        """Админ может писать в WIP."""
        self.assertTrue(check_zone_permission(self.admin, self.wip_folder, self.section))

    def test_wip_viewer_cannot_write(self):
        """Наблюдатель не может писать в WIP."""
        self.assertFalse(
            check_zone_permission(self.viewer, self.wip_folder, self.section)
        )

    def test_shared_only_gip_can_write(self):
        """В SHARED может писать только ГИП проекта и админ."""
        self.assertTrue(
            check_zone_permission(self.gip, self.shared_folder, self.section)
        )
        self.assertTrue(
            check_zone_permission(self.admin, self.shared_folder, self.section)
        )
        self.assertFalse(
            check_zone_permission(self.developer, self.shared_folder, self.section)
        )
        self.assertFalse(
            check_zone_permission(self.viewer, self.shared_folder, self.section)
        )

    def test_published_only_admin_can_write(self):
        """В PUBLISHED может писать только админ."""
        self.assertTrue(
            check_zone_permission(self.admin, self.published_folder, self.section)
        )
        self.assertFalse(
            check_zone_permission(self.gip, self.published_folder, self.section)
        )
        self.assertFalse(
            check_zone_permission(self.developer, self.published_folder, self.section)
        )
        self.assertFalse(
            check_zone_permission(self.viewer, self.published_folder, self.section)
        )

    def test_archive_admin_can_write_non_admin_cannot(self):
        """В ARCHIVE может писать только админ (перемещать/загружать)."""
        self.assertTrue(
            check_zone_permission(self.admin, self.archive_folder, self.section)
        )
        self.assertFalse(
            check_zone_permission(self.gip, self.archive_folder, self.section)
        )
        self.assertFalse(
            check_zone_permission(self.developer, self.archive_folder, self.section)
        )
        self.assertFalse(
            check_zone_permission(self.viewer, self.archive_folder, self.section)
        )

    def test_none_folder_uses_standard_permissions(self):
        """При folder=None используются стандартные правила."""
        self.assertTrue(
            check_zone_permission(self.developer, None, self.section)
        )
        self.assertFalse(
            check_zone_permission(self.viewer, None, self.section)
        )


class ZoneUploadPermissionTest(TestCase):
    """Тесты загрузки документов с учётом зон."""

    def setUp(self):
        data = _create_test_data()
        self.admin = data['admin']
        self.gip = data['gip']
        self.developer = data['developer']
        self.project = data['project']
        self.section = data['section']

        self.wip_folder = Folder.objects.get(project=self.project, zone='WIP')
        self.shared_folder = Folder.objects.get(project=self.project, zone='SHARED')
        self.published_folder = Folder.objects.get(
            project=self.project, zone='PUBLISHED'
        )
        self.archive_folder = Folder.objects.get(project=self.project, zone='ARCHIVE')

    def _make_file(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(
            'test.pdf', b'%PDF-1.4 test content', content_type='application/pdf'
        )

    def test_developer_cannot_upload_to_published(self):
        """Разработчик не может загружать в PUBLISHED."""
        with self.assertRaises(PermissionDenied):
            upload_document(
                user=self.developer,
                project=self.project,
                section=self.section,
                code='TEST-002',
                name='Test',
                doc_type='drawing',
                file=self._make_file(),
                folder=self.published_folder,
            )

    def test_developer_cannot_upload_to_archive(self):
        """Разработчик не может загружать в ARCHIVE."""
        with self.assertRaises(PermissionDenied):
            upload_document(
                user=self.developer,
                project=self.project,
                section=self.section,
                code='TEST-003',
                name='Test',
                doc_type='drawing',
                file=self._make_file(),
                folder=self.archive_folder,
            )

    def test_admin_can_upload_to_any_zone(self):
        """Админ может загружать в WIP, SHARED, PUBLISHED и ARCHIVE."""
        # Админ может загружать в WIP
        upload_document(
            user=self.admin,
            project=self.project,
            section=self.section,
            code='TEST-004',
            name='Admin Upload',
            doc_type='drawing',
            file=self._make_file(),
            folder=self.wip_folder,
        )
        # Админ может загружать в SHARED
        shared_folder = Folder.objects.get(project=self.project, zone='SHARED')
        upload_document(
            user=self.admin,
            project=self.project,
            section=self.section,
            code='TEST-005',
            name='Admin Upload 2',
            doc_type='drawing',
            file=self._make_file(),
            folder=shared_folder,
        )
        # Админ может загружать в PUBLISHED
        upload_document(
            user=self.admin,
            project=self.project,
            section=self.section,
            code='TEST-006',
            name='Admin Upload 3',
            doc_type='drawing',
            file=self._make_file(),
            folder=self.published_folder,
        )
        # Админ может загружать в ARCHIVE
        upload_document(
            user=self.admin,
            project=self.project,
            section=self.section,
            code='TEST-007',
            name='Admin Upload 4',
            doc_type='drawing',
            file=self._make_file(),
            folder=self.archive_folder,
        )


class EnsureProjectZonesTest(TestCase):
    """Тесты для _ensure_project_zones (старые проекты)."""

    def setUp(self):
        data = _create_test_data()
        self.admin = data['admin']
        self.project = data['project']
        self.section = data['section']

    def test_ensure_zones_creates_missing_zones(self):
        """_ensure_project_zones создаёт зоны для проекта без зон."""
        # Удаляем зоны
        Folder.objects.filter(project=self.project).delete()
        self.assertEqual(
            Folder.objects.filter(project=self.project).count(), 0
        )

        # Вызываем ensure
        result = _ensure_project_zones(self.project, self.admin)
        self.assertTrue(result)

        # Проверяем, что зоны созданы
        zones = Folder.objects.filter(
            project=self.project, zone__in=dict(Folder.ZONE_CHOICES)
        )
        self.assertEqual(zones.count(), 4)

    def test_ensure_zones_noop_when_zones_exist(self):
        """_ensure_project_zones не создаёт дубликаты."""
        zones_before = Folder.objects.filter(
            project=self.project, zone__in=dict(Folder.ZONE_CHOICES)
        ).count()

        result = _ensure_project_zones(self.project, self.admin)
        self.assertFalse(result)

        zones_after = Folder.objects.filter(
            project=self.project, zone__in=dict(Folder.ZONE_CHOICES)
        ).count()
        self.assertEqual(zones_before, zones_after)

    def test_ensure_zones_partial_zones(self):
        """Бэкфилл создаёт только отсутствующие зоны."""
        # Удаляем все зоны
        Folder.objects.filter(project=self.project).delete()
        # Создаём только WIP
        Folder.objects.create(
            project=self.project, section=None, parent=None,
            name='01. В работе', order=1, zone='WIP',
            created_by=self.admin,
        )
        # Бэкфилл
        result = _ensure_project_zones(self.project, self.admin)
        self.assertTrue(result)
        # Должны быть все 4
        zones = Folder.objects.filter(
            project=self.project, zone__in=dict(Folder.ZONE_CHOICES)
        )
        self.assertEqual(zones.count(), 4)

    def test_ensure_zones_project_with_regular_folder(self):
        """Проект с обычной папкой (без зон) → после бэкфилла появляются 4 зоны."""
        # Удаляем зоны
        Folder.objects.filter(project=self.project).delete()
        # Создаём обычную папку (без zone)
        Folder.objects.create(
            project=self.project, section=None, parent=None,
            name='Обычная папка', order=10, zone='WIP',
            created_by=self.admin,
        )
        # Бэкфилл должен создать все 4 канонические зоны
        result = _ensure_project_zones(self.project, self.admin)
        self.assertTrue(result)
        zones = Folder.objects.filter(
            project=self.project, zone__in=dict(Folder.ZONE_CHOICES),
            parent__isnull=True, section__isnull=True,
        )
        self.assertEqual(zones.count(), 4)


class MoveDocumentZoneCheckTest(TestCase):
    """B1: move_document() проверяет зоны."""

    def setUp(self):
        data = _create_test_data()
        self.admin = data['admin']
        self.gip = data['gip']
        self.developer = data['developer']
        self.project = data['project']
        self.section = data['section']

        self.wip_folder = Folder.objects.get(project=self.project, zone='WIP')
        self.shared_folder = Folder.objects.get(project=self.project, zone='SHARED')
        self.published_folder = Folder.objects.get(
            project=self.project, zone='PUBLISHED'
        )
        self.archive_folder = Folder.objects.get(project=self.project, zone='ARCHIVE')

    def _create_document(self, folder=None):
        from django.core.files.uploadedfile import SimpleUploadedFile

        file = SimpleUploadedFile(
            'test.pdf', b'%PDF-1.4 test content', content_type='application/pdf'
        )
        upload_document(
            user=self.admin,
            project=self.project,
            section=self.section,
            code='TEST-MOVE',
            name='Test Document',
            doc_type='drawing',
            file=file,
            folder=folder,
        )
        return Document.objects.get(
            project=self.project, section=self.section, code='TEST-MOVE'
        )

    def test_developer_cannot_move_to_archive(self):
        """Разработчик не может переместить документ в ARCHIVE."""
        doc = self._create_document(self.wip_folder)
        with self.assertRaises(PermissionDenied):
            move_document(self.developer, doc, self.archive_folder)

    def test_developer_cannot_move_to_published(self):
        """Разработчик не может переместить документ в PUBLISHED."""
        doc = self._create_document(self.wip_folder)
        with self.assertRaises(PermissionDenied):
            move_document(self.developer, doc, self.published_folder)

    def test_developer_cannot_move_to_shared(self):
        """Разработчик не может переместить документ в SHARED."""
        doc = self._create_document(self.wip_folder)
        with self.assertRaises(PermissionDenied):
            move_document(self.developer, doc, self.shared_folder)

    def test_archive_document_only_admin_can_move(self):
        """Документ в ARCHIVE может переместить только админ."""
        doc = self._create_document(self.archive_folder)
        # ГИП не может
        with self.assertRaises(PermissionDenied):
            move_document(self.gip, doc, self.wip_folder)
        # Разработчик не может
        with self.assertRaises(PermissionDenied):
            move_document(self.developer, doc, self.wip_folder)
        # Админ может
        move_document(self.admin, doc, self.wip_folder)
        doc.refresh_from_db()
        self.assertEqual(doc.folder, self.wip_folder)


class UploadDocumentZoneCheckTest(TestCase):
    """B2: upload_document() проверяет зону текущей папки документа."""

    def setUp(self):
        data = _create_test_data()
        self.admin = data['admin']
        self.developer = data['developer']
        self.project = data['project']
        self.section = data['section']

        self.archive_folder = Folder.objects.get(project=self.project, zone='ARCHIVE')

    def _make_file(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(
            'test.pdf', b'%PDF-1.4 test content', content_type='application/pdf'
        )

    def _create_document_in_archive(self):
        """Создаёт документ в ARCHIVE."""
        file = self._make_file()
        upload_document(
            user=self.admin,
            project=self.project,
            section=self.section,
            code='TEST-UPLOAD',
            name='Archive Doc',
            doc_type='drawing',
            file=file,
            folder=self.archive_folder,
        )
        return Document.objects.get(
            project=self.project, section=self.section, code='TEST-UPLOAD'
        )

    def test_developer_cannot_upload_to_archive_document(self):
        """Разработчик не может загрузить версию в документ из ARCHIVE."""
        doc = self._create_document_in_archive()
        with self.assertRaises(PermissionDenied):
            upload_document(
                user=self.developer,
                project=self.project,
                section=self.section,
                code='TEST-UPLOAD',  # тот же code → обновит существующий
                name='Archive Doc',
                doc_type='drawing',
                file=self._make_file(),
                folder=None,  # folder=None, но документ уже в ARCHIVE
            )


class CopyDocumentZoneCheckTest(TestCase):
    """B3: copy_document() проверяет зону."""

    def setUp(self):
        data = _create_test_data()
        self.admin = data['admin']
        self.developer = data['developer']
        self.project = data['project']
        self.section = data['section']

        self.wip_folder = Folder.objects.get(project=self.project, zone='WIP')
        self.archive_folder = Folder.objects.get(project=self.project, zone='ARCHIVE')
        self.published_folder = Folder.objects.get(
            project=self.project, zone='PUBLISHED'
        )

    def _make_file(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(
            'test.pdf', b'%PDF-1.4 test content', content_type='application/pdf'
        )

    def _create_document(self):
        from storage.domain.services import upload_document

        file = self._make_file()
        upload_document(
            user=self.admin,
            project=self.project,
            section=self.section,
            code='TEST-COPY',
            name='Copy Test',
            doc_type='drawing',
            file=file,
            folder=self.wip_folder,
        )
        return Document.objects.get(
            project=self.project, section=self.section, code='TEST-COPY'
        )

    def test_developer_cannot_copy_to_archive(self):
        """Разработчик не может копировать документ в ARCHIVE."""
        from storage.domain.services import copy_document

        doc = self._create_document()
        with self.assertRaises(PermissionDenied):
            copy_document(
                user=self.developer,
                document=doc,
                folder=self.archive_folder,
                section=None,
                file_storage=None,
            )

    def test_developer_cannot_copy_to_published(self):
        """Разработчик не может копировать документ в PUBLISHED."""
        from storage.domain.services import copy_document

        doc = self._create_document()
        with self.assertRaises(PermissionDenied):
            copy_document(
                user=self.developer,
                document=doc,
                folder=self.published_folder,
                section=None,
                file_storage=None,
            )


class MoveToRegularFolderStatusTest(TestCase):
    """B5: Перемещение в обычную папку не меняет статус."""

    def setUp(self):
        data = _create_test_data()
        self.admin = data['admin']
        self.project = data['project']
        self.section = data['section']

        self.wip_folder = Folder.objects.get(project=self.project, zone='WIP')
        # Создаём обычную папку (не каноническую зону)
        self.regular_folder = Folder.objects.create(
            project=self.project,
            section=self.section,
            parent=None,
            name='Обычная папка',
            order=100,
            zone='WIP',
            created_by=self.admin,
        )

    def _create_document(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        file = SimpleUploadedFile(
            'test.pdf', b'%PDF-1.4 test content', content_type='application/pdf'
        )
        upload_document(
            user=self.admin,
            project=self.project,
            section=self.section,
            code='TEST-STATUS',
            name='Test Doc',
            doc_type='drawing',
            file=file,
            folder=self.wip_folder,
        )
        return Document.objects.get(
            project=self.project, section=self.section, code='TEST-STATUS'
        )

    def test_move_to_regular_folder_preserves_status(self):
        """Перемещение в обычную папку не меняет статус."""
        doc = self._create_document()
        self.assertEqual(doc.status, 'draft')

        # Перемещаем в обычную папку
        move_document(self.admin, doc, self.regular_folder)
        doc.refresh_from_db()

        # Статус должен остаться draft (не изменился)
        self.assertEqual(doc.status, 'draft')

    def test_move_to_regular_folder_from_approved(self):
        """Перемещение из PUBLISHED в обычную папку не сбрасывает статус."""
        doc = self._create_document()
        # Сначала в PUBLISHED
        published = Folder.objects.get(project=self.project, zone='PUBLISHED')
        move_document(self.admin, doc, published)
        doc.refresh_from_db()
        self.assertEqual(doc.status, 'approved')

        # Теперь в обычную папку
        move_document(self.admin, doc, self.regular_folder)
        doc.refresh_from_db()

        # Статус должен остаться approved
        self.assertEqual(doc.status, 'approved')


class ArchiveEscapePreventionTest(TestCase):
    """B2: Проверка что документ не может "убежать" из ARCHIVE."""

    def setUp(self):
        data = _create_test_data()
        self.admin = data['admin']
        self.developer = data['developer']
        self.project = data['project']
        self.section = data['section']

        self.archive_folder = Folder.objects.get(project=self.project, zone='ARCHIVE')
        self.wip_folder = Folder.objects.get(project=self.project, zone='WIP')

    def _make_file(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(
            'test.pdf', b'%PDF-1.4 test content', content_type='application/pdf'
        )

    def _create_document_in_archive(self):
        """Создаёт документ в ARCHIVE (через WIP + move)."""
        from storage.domain.services import move_document

        # Сначала создаём в WIP
        file = self._make_file()
        upload_document(
            user=self.admin,
            project=self.project,
            section=self.section,
            code='TEST-ESCAPE',
            name='Archive Doc',
            doc_type='drawing',
            file=file,
            folder=self.wip_folder,
        )
        doc = Document.objects.get(
            project=self.project, section=self.section, code='TEST-ESCAPE'
        )
        # Перемещаем в ARCHIVE (устанавливает статус 'archived')
        move_document(self.admin, doc, self.archive_folder)
        doc.refresh_from_db()
        return doc

    def test_developer_cannot_upload_to_archive_via_wip_folder(self):
        """Разработчик не может загрузить версию в документ из ARCHICE через folder=WIP.

        Документ уже в ARCHIVE, инженер пытается передать folder=WIP.
        upload_document должен проверить текущую зону документа и запретить.
        """
        doc = self._create_document_in_archive()
        self.assertEqual(doc.folder, self.archive_folder)
        self.assertEqual(doc.status, 'archived')

        # Разработчик пытается загрузить версию с folder=WIP
        with self.assertRaises(PermissionDenied):
            upload_document(
                user=self.developer,
                project=self.project,
                section=self.section,
                code='TEST-ESCAPE',  # тот же code → обновит существующий
                name='Archive Doc',
                doc_type='drawing',
                file=self._make_file(),
                folder=self.wip_folder,  # пытается «убежать» в WIP
            )

        # Документ остался в архиве
        doc.refresh_from_db()
        self.assertEqual(doc.folder, self.archive_folder)
        self.assertEqual(doc.status, 'archived')

    def test_upload_to_archive_document_forbidden_for_admin(self):
        """Админ не может загрузить версию в документ из ARCHIVE."""
        doc = self._create_document_in_archive()
        self.assertEqual(doc.folder, self.archive_folder)

        for target in (None, self.wip_folder):
            with self.assertRaises(PermissionDenied):
                upload_document(
                    user=self.admin,
                    project=self.project,
                    section=self.section,
                    code='TEST-ESCAPE',
                    name='Archive Doc v2',
                    doc_type='drawing',
                    file=self._make_file(),
                    folder=target,
                )

        doc.refresh_from_db()
        self.assertEqual(doc.folder, self.archive_folder)
        self.assertEqual(doc.status, 'archived')


class FolderTreeTemplateTest(TestCase):
    """B6: Проверка что шаблон _folder_tree.html компилируется."""

    def setUp(self):
        data = _create_test_data()
        self.admin = data['admin']
        self.project = data['project']
        self.section = data['section']

    def _make_folder_context(self, zone_code, zone_name):
        """Создаёт Folder с атрибутами, нужными шаблону."""
        folder = Folder.objects.create(
            project=self.project,
            section=None,
            parent=None,
            name=zone_name,
            order=1,
            zone=zone_code,
            created_by=self.admin,
        )
        # Шаблон требует document_count и subtree_document_count
        folder.document_count = 0
        folder.subtree_document_count = 0
        folder.children_tree = []
        return folder

    def test_folder_tree_template_includes_all_zones(self):
        """Шаблон рендерится без TemplateSyntaxError для всех зон."""
        from django.template import Template, Context

        template_content = open(
            'storage/adapters/templates/storage/_folder_tree.html'
        ).read()
        template = Template(template_content)

        for zone_code, zone_name in Folder.ZONE_CHOICES:
            folder = self._make_folder_context(zone_code, zone_name)
            context = Context({
                'folder': folder,
                'project': self.project,
                'selected_folder': folder,
                'can_manage_structure': True,
                'selected_view': None,
            })
            result = template.render(context)
            self.assertIn(zone_name, result)
            self.assertIn(str(folder.pk), result)

    def test_folder_tree_template_wip_icon(self):
        """Для WIP-зоны рендерится иконка карандаша."""
        from django.template import Template, Context

        template_content = open(
            'storage/adapters/templates/storage/_folder_tree.html'
        ).read()
        template = Template(template_content)

        folder = self._make_folder_context('WIP', '01. В работе')
        context = Context({
            'folder': folder,
            'project': self.project,
            'selected_folder': folder,
            'can_manage_structure': True,
            'selected_view': None,
        })
        result = template.render(context)
        self.assertIn('bi-pencil-square', result)

    def test_folder_tree_template_archive_icon(self):
        """Для ARCHIVE-зоны рендерится иконка замка."""
        from django.template import Template, Context

        template_content = open(
            'storage/adapters/templates/storage/_folder_tree.html'
        ).read()
        template = Template(template_content)

        folder = self._make_folder_context('ARCHIVE', '04. Архив')
        context = Context({
            'folder': folder,
            'project': self.project,
            'selected_folder': folder,
            'can_manage_structure': True,
            'selected_view': None,
        })
        result = template.render(context)
        self.assertIn('bi-lock', result)
        self.assertNotIn('bi-pencil-square', result)

    def test_folder_tree_template_published_icon(self):
        """Для PUBLISHED-зоны рендерится иконка щита."""
        from django.template import Template, Context

        template_content = open(
            'storage/adapters/templates/storage/_folder_tree.html'
        ).read()
        template = Template(template_content)

        folder = self._make_folder_context('PUBLISHED', '03. Опубликовано')
        context = Context({
            'folder': folder,
            'project': self.project,
            'selected_folder': folder,
            'can_manage_structure': True,
            'selected_view': None,
        })
        result = template.render(context)
        self.assertIn('bi-shield-check', result)
        self.assertNotIn('bi-pencil-square', result)

    def test_folder_tree_template_shared_icon(self):
        """Для SHARED-зоны рендерится иконка людей."""
        from django.template import Template, Context

        template_content = open(
            'storage/adapters/templates/storage/_folder_tree.html'
        ).read()
        template = Template(template_content)

        folder = self._make_folder_context('SHARED', '02. На согласовании')
        context = Context({
            'folder': folder,
            'project': self.project,
            'selected_folder': folder,
            'can_manage_structure': True,
            'selected_view': None,
        })
        result = template.render(context)
        self.assertIn('bi-people', result)
        self.assertNotIn('bi-pencil-square', result)
