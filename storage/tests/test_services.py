from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from storage.domain.models import (
    Company,
    Document,
    Project,
    ProjectMembership,
    Section,
    UserProfile,
)
from storage.domain.services import (
    check_read_permission,
    check_write_permission,
    create_project,
    generate_vedomost,
    upload_document,
)


User = get_user_model()


class ServiceTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Компания 1')
        self.other_company = Company.objects.create(name='Компания 2')

        self.user_admin = User.objects.create_user('admin', 'admin@test.com', 'adminpass')
        self.user_gip = User.objects.create_user('gip', 'gip@test.com', 'gippass')
        self.user_engineer = User.objects.create_user('engineer', 'eng@test.com', 'engpass')
        self.user_viewer = User.objects.create_user('viewer', 'view@test.com', 'viewpass')
        self.user_contractor = User.objects.create_user('contr', 'con@test.com', 'conpass')

        UserProfile.objects.create(user=self.user_admin, role='admin', company=self.company)
        UserProfile.objects.create(user=self.user_gip, role='gip', company=self.company)
        UserProfile.objects.create(user=self.user_engineer, role='engineer', company=self.company)
        UserProfile.objects.create(user=self.user_viewer, role='viewer', company=self.company)
        UserProfile.objects.create(user=self.user_contractor, role='contractor', company=self.company)

        self.project_own = Project.objects.create(
            company=self.company,
            code='PROJ-01',
            name='Проект 01',
            customer='Заказчик',
            gip=self.user_gip,
            status='active',
            created_by=self.user_admin,
        )
        self.project_other = Project.objects.create(
            company=self.company,
            code='PROJ-02',
            name='Проект 02',
            customer='Заказчик 2',
            status='active',
            created_by=self.user_admin,
        )
        self.foreign_project = Project.objects.create(
            company=self.other_company,
            code='PROJ-FOREIGN',
            name='Чужой проект',
            customer='Чужой заказчик',
            status='active',
            created_by=self.user_admin,
        )

        self.section_own = Section.objects.create(
            project=self.project_own,
            code='Секция А',
            name='Секция А',
            order=1,
        )
        self.section_other = Section.objects.create(
            project=self.project_other,
            code='Секция Б',
            name='Секция Б',
            order=1,
        )
        self.foreign_section = Section.objects.create(
            project=self.foreign_project,
            code='Секция В',
            name='Секция В',
            order=1,
        )

        ProjectMembership.objects.create(
            project=self.project_own,
            user=self.user_gip,
            role_in_project='gip',
        )

        self.user_engineer.userprofile.allowed_sections.add(self.section_own)

        self.document = Document.objects.create(
            project=self.project_own,
            section=self.section_own,
            code='ДО-001',
            name='Чертеж А',
            doc_type='drawing',
            status='draft',
            created_by=self.user_admin,
        )

    def test_check_write_permission(self):
        self.assertTrue(check_write_permission(self.user_admin, self.section_own))

        self.assertTrue(check_write_permission(self.user_gip, self.section_own))
        self.assertFalse(check_write_permission(self.user_gip, self.section_other))

        self.assertTrue(check_write_permission(self.user_engineer, self.section_own))
        self.assertFalse(check_write_permission(self.user_engineer, self.section_other))

        self.assertFalse(check_write_permission(self.user_viewer, self.section_own))

        self.assertFalse(check_write_permission(self.user_contractor, self.section_own))

    def test_check_write_permission_denied_across_companies(self):
        self.assertFalse(check_write_permission(self.user_admin, self.foreign_section))
        self.assertFalse(check_write_permission(self.user_gip, self.foreign_section))

    def test_check_read_permission_denied_across_companies(self):
        foreign_document = Document.objects.create(
            project=self.foreign_project,
            section=self.foreign_section,
            code='ЧУЖ-001',
            name='Чужой чертеж',
            doc_type='drawing',
            status='draft',
            created_by=self.user_admin,
        )
        self.assertFalse(check_read_permission(self.user_viewer, foreign_document))
        self.assertFalse(check_read_permission(self.user_admin, foreign_document))

    def test_superuser_without_profile_has_access(self):
        superuser = User.objects.create_superuser('root', 'root@test.com', 'rootpass')
        foreign_document = Document.objects.create(
            project=self.foreign_project,
            section=self.foreign_section,
            code='ЧУЖ-003',
            name='Чужой чертеж',
            doc_type='drawing',
            status='draft',
            created_by=self.user_admin,
        )
        self.assertTrue(check_write_permission(superuser, self.foreign_section))
        self.assertTrue(check_read_permission(superuser, foreign_document))

    def test_create_project(self):
        project = create_project(
            company=self.company,
            code='NEW-01',
            name='Новый проект',
            customer='Заказчик',
            gip=self.user_gip,
            created_by=self.user_gip,
        )
        self.assertEqual(project.company, self.company)
        self.assertEqual(project.gip, self.user_gip)
        self.assertEqual(project.created_by, self.user_gip)
        self.assertEqual(project.status, 'active')

    def test_create_project_denied_for_foreign_company(self):
        with self.assertRaises(PermissionDenied):
            create_project(
                company=self.other_company,
                code='NEW-02',
                name='Чужой проект',
                customer='Заказчик',
                gip=self.user_gip,
                created_by=self.user_gip,
            )

    def test_create_project_denied_for_foreign_gip(self):
        foreign_gip = User.objects.create_user('foreign_gip', 'fg@test.com', 'fgpass')
        UserProfile.objects.create(
            user=foreign_gip,
            role='gip',
            company=self.other_company,
        )
        with self.assertRaises(PermissionDenied):
            create_project(
                company=self.company,
                code='NEW-03',
                name='Проект',
                customer='Заказчик',
                gip=foreign_gip,
                created_by=self.user_admin,
            )

    def test_upload_document_first_version(self):
        file = SimpleUploadedFile('test.pdf', b'file content', content_type='application/pdf')
        version = upload_document(
            user=self.user_gip,
            project=self.project_own,
            section=self.section_own,
            code='ДО-001',
            name='Чертеж А',
            doc_type='drawing',
            file=file,
        )
        self.assertEqual(version.version_number, 1)
        self.document.refresh_from_db()
        self.assertEqual(version.id, self.document.current_version_id)

    def test_upload_document_second_version(self):
        file1 = SimpleUploadedFile('test1.pdf', b'content 1', content_type='application/pdf')
        upload_document(
            user=self.user_gip,
            project=self.project_own,
            section=self.section_own,
            code='ДО-001',
            name='Чертеж А',
            doc_type='drawing',
            file=file1,
        )
        file2 = SimpleUploadedFile('test2.pdf', b'content 2', content_type='application/pdf')
        version = upload_document(
            user=self.user_gip,
            project=self.project_own,
            section=self.section_own,
            code='ДО-001',
            name='Чертеж А',
            doc_type='drawing',
            file=file2,
        )
        self.assertEqual(version.version_number, 2)
        self.document.refresh_from_db()
        self.assertEqual(version.id, self.document.current_version_id)

    def test_upload_document_permission_denied(self):
        with self.assertRaises(PermissionDenied):
            upload_document(
                user=self.user_engineer,
                project=self.project_own,
                section=self.section_other,
                code='ДО-002',
                name='Чертеж Б',
                doc_type='drawing',
                file=SimpleUploadedFile('test.pdf', b'content', content_type='application/pdf'),
            )

    def test_upload_document_denied_across_companies(self):
        with self.assertRaises(PermissionDenied):
            upload_document(
                user=self.user_gip,
                project=self.foreign_project,
                section=self.foreign_section,
                code='ЧУЖ-002',
                name='Чужой чертеж',
                doc_type='drawing',
                file=SimpleUploadedFile('test.pdf', b'content', content_type='application/pdf'),
            )

    def test_upload_document_wrong_project(self):
        with self.assertRaises(ValueError) as ctx:
            upload_document(
                user=self.user_gip,
                project=self.project_other,
                section=self.section_own,
                code='ДО-003',
                name='Чертеж В',
                doc_type='drawing',
                file=SimpleUploadedFile('test.pdf', b'content', content_type='application/pdf'),
            )
        self.assertIn('Раздел не относится к указанному проекту', str(ctx.exception))

    def test_generate_vedomost(self):
        Document.objects.create(
            project=self.project_own,
            section=self.section_own,
            code='ДО-002',
            name='Чертеж Б',
            doc_type='drawing',
            status='draft',
            created_by=self.user_admin,
        )
        vedomost = generate_vedomost(self.project_own, company=self.company)
        self.assertIsInstance(vedomost, list)
        self.assertGreaterEqual(len(vedomost), 2)
        item = vedomost[0]
        self.assertIn('code', item)
        self.assertIn('name', item)
        self.assertIn('doc_type', item)
        self.assertIn('version', item)
        self.assertIn('date', item)
        self.assertIn('author', item)
        self.assertEqual(vedomost[0]['code'], 'ДО-001')
        self.assertEqual(vedomost[1]['code'], 'ДО-002')

    def test_generate_vedomost_denied_for_foreign_company(self):
        with self.assertRaises(PermissionDenied):
            generate_vedomost(self.foreign_project, company=self.company)
