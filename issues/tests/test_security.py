import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from issues.models import Issue, IssueComment
from storage.domain.models import (
    Company,
    Project,
    ProjectMembership,
    Section,
    UserProfile,
)
from storage.domain.services import (
    create_issue,
    update_issue_status,
    upload_document,
)


User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class IssueSecurityTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Компания А')
        self.other_company = Company.objects.create(name='Компания Б')

        self.gip_a = User.objects.create_user('gip_a', password='pass')
        UserProfile.objects.create(
            user=self.gip_a, role='gip', company=self.company
        )
        self.viewer_a = User.objects.create_user('viewer_a', password='pass')
        UserProfile.objects.create(
            user=self.viewer_a, role='viewer', company=self.company
        )
        self.developer_a = User.objects.create_user(
            'developer_a', password='pass'
        )
        UserProfile.objects.create(
            user=self.developer_a, role='developer', company=self.company
        )
        self.admin_b = User.objects.create_user('admin_b', password='pass')
        UserProfile.objects.create(
            user=self.admin_b, role='admin', company=self.other_company
        )

        self.project = Project.objects.create(
            company=self.company,
            code='SEC-1',
            name='Проект А',
            status='active',
            gip=self.gip_a,
            created_by=self.gip_a,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.developer_a,
            role_in_project='developer',
        )
        self.section = Section.objects.create(
            project=self.project,
            code='АР',
            name='Архитектура',
            order=1,
        )
        self.version = upload_document(
            user=self.gip_a,
            project=self.project,
            section=self.section,
            code='SEC-01',
            name='Документ',
            doc_type='drawing',
            file=SimpleUploadedFile(
                'doc.pdf', b'data', content_type='application/pdf'
            ),
        )
        self.document = self.version.document
        self.issue = create_issue(
            user=self.gip_a,
            document=self.document,
            description='Замечание',
            version=self.version,
        )

    def test_viewer_cannot_add_comment(self):
        self.client.force_login(self.viewer_a)
        response = self.client.post(
            reverse('issues:add_comment', args=[self.issue.pk]),
            {'text': 'Комментарий наблюдателя'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(IssueComment.objects.exists())

    def test_user_from_other_company_cannot_add_comment(self):
        self.client.force_login(self.admin_b)
        response = self.client.post(
            reverse('issues:add_comment', args=[self.issue.pk]),
            {'text': 'Чужой комментарий'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(IssueComment.objects.exists())

    def test_admin_from_other_company_cannot_delete_crop(self):
        self.client.force_login(self.admin_b)
        response = self.client.post(
            reverse('issues:issue_delete_crop', args=[self.issue.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_create_issue_ignores_actual_fix_date(self):
        self.client.force_login(self.gip_a)
        response = self.client.post(
            reverse('issues:issue_create', args=[self.document.pk]),
            {
                'description': 'Замечание с поддельной датой',
                'actual_fix_date': '2026-01-01',
                'version_id': self.version.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        issue = Issue.objects.latest('pk')
        self.assertIsNone(issue.actual_fix_date)

    def test_developer_cannot_close_issue(self):
        with self.assertRaises(PermissionDenied):
            update_issue_status(self.developer_a, self.issue, 'CLOSED')

    def test_gip_can_close_issue(self):
        update_issue_status(self.gip_a, self.issue, 'CLOSED')
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, 'CLOSED')

    def test_closed_issue_sets_dates(self):
        update_issue_status(self.gip_a, self.issue, 'CLOSED')
        self.issue.refresh_from_db()
        self.assertIsNotNone(self.issue.closed_at)
        self.assertIsNotNone(self.issue.actual_fix_date)
