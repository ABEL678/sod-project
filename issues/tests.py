import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from issues.models import Issue
from storage.domain.models import Project, Section, UserProfile
from storage.domain.services import upload_document


User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class IssueTests(TestCase):
    def setUp(self):
        self.user_gip = User.objects.create_user('gip', password='pass')
        UserProfile.objects.create(user=self.user_gip, role='gip')

        self.user_viewer = User.objects.create_user('viewer', password='pass')
        UserProfile.objects.create(user=self.user_viewer, role='viewer')

        self.project = Project.objects.create(
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
        UserProfile.objects.create(user=contractor, role='contractor')
        self.client.force_login(contractor)
        response = self.client.post(
            reverse('issues:issue_create', args=[self.document.pk]),
            {'description': 'Чужое замечание'},
        )
        self.assertEqual(response.status_code, 403)
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
