import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from issues.models import Issue
from storage.domain.models import Company, Document, Project, Section, UserProfile
from storage.domain.services import create_issue, upload_document


User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class IssueTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Компания 1')

        self.user_gip = User.objects.create_user('gip', password='pass')
        UserProfile.objects.create(user=self.user_gip, role='gip', company=self.company)

        self.user_viewer = User.objects.create_user('viewer', password='pass')
        UserProfile.objects.create(user=self.user_viewer, role='viewer', company=self.company)

        self.project = Project.objects.create(
            company=self.company,
            code='P-1',
            name='Проект',
            status='active',
            gip=self.user_gip,
            created_by=self.user_gip,
        )
        self.section = Section.objects.create(
            project=self.project,
            code='АР',
            name='Архитектурные решения',
            order=1,
        )

        self.version = upload_document(
            user=self.user_gip,
            project=self.project,
            section=self.section,
            code='АР-01',
            name='План 1 этажа',
            doc_type='drawing',
            file=SimpleUploadedFile('plan.pdf', b'data', content_type='application/pdf'),
        )
        self.document = self.version.document

    def test_create_issue(self):
        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('issues:issue_create', args=[self.document.pk]),
            {
                'description': 'Не указана отметка 0.000',
                'version_id': self.version.pk,
            },
        )
        self.assertRedirects(
            response,
            reverse('storage:document_detail', args=[self.document.pk]),
        )
        issue = Issue.objects.get()
        self.assertEqual(issue.document, self.document)
        self.assertEqual(issue.version, self.version)
        self.assertEqual(issue.author, self.user_gip)
        self.assertEqual(issue.status, 'open')

    def test_create_issue_sets_current_version_by_default(self):
        self.client.force_login(self.user_gip)
        self.client.post(
            reverse('issues:issue_create', args=[self.document.pk]),
            {'description': 'Замечание без явной версии'},
        )
        issue = Issue.objects.get()
        self.assertEqual(issue.version, self.document.current_version)

    def test_create_issue_requires_read_permission(self):
        contractor = User.objects.create_user('contractor', password='pass')
        UserProfile.objects.create(user=contractor, role='contractor', company=self.company)
        self.client.force_login(contractor)
        response = self.client.post(
            reverse('issues:issue_create', args=[self.document.pk]),
            {'description': 'Чужое замечание'},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Issue.objects.count(), 0)

    def test_create_issue_contractor_foreign_section_not_found(self):
        other_section = Section.objects.create(
            project=self.project,
            code='КР',
            name='Конструктивные решения',
            order=2,
        )
        other_document = Document.objects.create(
            project=self.project,
            section=other_section,
            code='КР-01',
            name='Разрез',
            doc_type='drawing',
            status='draft',
            created_by=self.user_gip,
        )
        contractor = User.objects.create_user('contractor2', password='pass')
        profile = UserProfile.objects.create(
            user=contractor,
            role='contractor',
            company=self.company,
        )
        profile.allowed_sections.add(self.section)
        self.client.force_login(contractor)

        response = self.client.post(
            reverse('issues:issue_create', args=[other_document.pk]),
            {'description': 'Чужое замечание'},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Issue.objects.count(), 0)

    def test_toggle_status(self):
        issue = Issue.objects.create(
            document=self.document,
            version=self.version,
            author=self.user_gip,
            description='Замечание',
        )
        self.client.force_login(self.user_gip)

        response = self.client.post(
            reverse('issues:issue_status', args=[issue.pk]),
            {'action': 'close'},
        )
        self.assertRedirects(
            response,
            reverse('storage:document_detail', args=[self.document.pk]),
        )
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'closed')

        self.client.post(
            reverse('issues:issue_status', args=[issue.pk]),
            {'action': 'open'},
        )
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'open')

    def test_status_change_forbidden_for_stranger(self):
        issue = Issue.objects.create(
            document=self.document,
            version=self.version,
            author=self.user_gip,
            description='Замечание',
        )
        self.client.force_login(self.user_viewer)
        response = self.client.post(
            reverse('issues:issue_status', args=[issue.pk]),
            {'action': 'close'},
        )
        self.assertEqual(response.status_code, 403)
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'open')

    def test_status_change_contractor_foreign_section_not_found(self):
        other_section = Section.objects.create(
            project=self.project,
            code='КР',
            name='Конструктивные решения',
            order=2,
        )
        other_document = Document.objects.create(
            project=self.project,
            section=other_section,
            code='КР-02',
            name='Разрез',
            doc_type='drawing',
            status='draft',
            created_by=self.user_gip,
        )
        issue = Issue.objects.create(
            document=other_document,
            version=None,
            author=self.user_gip,
            description='Замечание в чужом разделе',
        )
        contractor = User.objects.create_user('contractor3', password='pass')
        profile = UserProfile.objects.create(
            user=contractor,
            role='contractor',
            company=self.company,
        )
        profile.allowed_sections.add(self.section)
        self.client.force_login(contractor)

        response = self.client.post(
            reverse('issues:issue_status', args=[issue.pk]),
            {'action': 'close'},
        )

        self.assertEqual(response.status_code, 404)
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'open')

    def test_create_issue_with_pin_coords(self):
        self.client.force_login(self.user_gip)
        issue = create_issue(
            user=self.user_gip,
            document=self.document,
            description='Замечание на пине',
            version=self.version,
            page_number=1,
            x_ratio=0.5,
            y_ratio=0.3,
        )
        self.assertEqual(issue.page_number, 1)
        self.assertEqual(issue.x_ratio, 0.5)
        self.assertEqual(issue.y_ratio, 0.3)

    def test_create_issue_x_y_without_page_number_raises(self):
        with self.assertRaises(ValueError):
            create_issue(
                user=self.user_gip,
                document=self.document,
                description='Координаты без номера страницы',
                page_number=None,
                x_ratio=0.5,
                y_ratio=0.5,
            )

    def test_create_issue_forbidden_for_viewer(self):
        with self.assertRaises(PermissionDenied):
            create_issue(
                user=self.user_viewer,
                document=self.document,
                description='Замечание без прав на запись',
            )

    def test_create_issue_view_forbidden_for_viewer(self):
        self.client.force_login(self.user_viewer)
        response = self.client.post(
            reverse('issues:issue_create', args=[self.document.pk]),
            {'description': 'Замечание без прав на запись'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Issue.objects.count(), 0)

    def test_create_issue_pin_coords_without_page_number(self):
        issue = create_issue(
            user=self.user_gip,
            document=self.document,
            description='Без пина',
        )
        self.assertIsNone(issue.page_number)
        self.assertIsNone(issue.x_ratio)
        self.assertIsNone(issue.y_ratio)

    def test_create_issue_missing_x_y_with_page_number(self):
        with self.assertRaises(ValueError):
            create_issue(
                user=self.user_gip,
                document=self.document,
                description='Пин без координат',
                page_number=1,
            )

    def test_create_issue_x_ratio_out_of_range(self):
        with self.assertRaises(ValueError):
            create_issue(
                user=self.user_gip,
                document=self.document,
                description='Координата вне диапазона',
                page_number=1,
                x_ratio=1.5,
                y_ratio=0.3,
            )

    def test_create_issue_version_not_from_document(self):
        other_section = Section.objects.create(
            project=self.project,
            code='КР',
            name='Конструктивные решения',
            order=2,
        )
        other_document = Document.objects.create(
            project=self.project,
            section=other_section,
            code='КР-99',
            name='Чужой документ',
            doc_type='drawing',
            status='draft',
            created_by=self.user_gip,
        )
        other_version = upload_document(
            user=self.user_gip,
            project=self.project,
            section=other_section,
            code='КР-99',
            name='Чужой документ',
            doc_type='drawing',
            file=SimpleUploadedFile('other.pdf', b'data', content_type='application/pdf'),
        )
        with self.assertRaises(ValueError):
            create_issue(
                user=self.user_gip,
                document=self.document,
                description='Версия от другого документа',
                version=other_version,
            )
