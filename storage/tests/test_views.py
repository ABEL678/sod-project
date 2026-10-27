import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from storage.domain.models import Project, Section, UserProfile


User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ViewTests(TestCase):
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
        self.project_other = Project.objects.create(
            code='P-2',
            name='Другой проект',
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
        self.section_other = Section.objects.create(
            project=self.project_other,
            code='КР',
            name='Конструктивные решения',
            order=1,
        )

    def test_project_list(self):
        response = self.client.get(reverse('storage:project_list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'storage/project_list.html')
        self.assertContains(response, 'P-1')

    def test_project_detail(self):
        response = self.client.get(reverse('storage:project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'storage/project_detail.html')
        self.assertContains(response, 'АР')
        self.assertContains(response, 'Перетащите файлы сюда или нажмите для выбора')
        self.assertContains(response, 'upload-ajax')

    def test_upload_ajax_success(self):
        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:document_upload_ajax', args=[self.project.pk]),
            {
                'section_id': self.section.pk,
                'code': 'АР-01',
                'name': 'План 1 этажа',
                'file': SimpleUploadedFile('plan.pdf', b'data', content_type='application/pdf'),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'version': 1})
        self.assertEqual(self.project.documents.count(), 1)
        self.assertEqual(self.project.documents.first().current_version.version_number, 1)

    def test_upload_ajax_second_version(self):
        self.client.force_login(self.user_gip)
        url = reverse('storage:document_upload_ajax', args=[self.project.pk])
        payload = {
            'section_id': self.section.pk,
            'code': 'АР-01',
            'name': 'План 1 этажа',
        }
        self.client.post(
            url,
            {**payload, 'file': SimpleUploadedFile('plan.pdf', b'data', content_type='application/pdf')},
        )
        response = self.client.post(
            url,
            {**payload, 'file': SimpleUploadedFile('plan2.pdf', b'data 2', content_type='application/pdf')},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'version': 2})

    def test_upload_ajax_permission_denied(self):
        self.client.force_login(self.user_viewer)
        response = self.client.post(
            reverse('storage:document_upload_ajax', args=[self.project.pk]),
            {
                'section_id': self.section.pk,
                'code': 'АР-02',
                'name': 'План',
                'file': SimpleUploadedFile('plan.pdf', b'data', content_type='application/pdf'),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('Нет прав на запись', response.json()['error'])
        self.assertEqual(self.project.documents.count(), 0)

    def test_upload_ajax_wrong_project(self):
        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:document_upload_ajax', args=[self.project.pk]),
            {
                'section_id': self.section_other.pk,
                'code': 'КР-01',
                'name': 'Разрез',
                'file': SimpleUploadedFile('plan.pdf', b'data', content_type='application/pdf'),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('Раздел не относится к указанному проекту', response.json()['error'])

    def test_upload_ajax_missing_section(self):
        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:document_upload_ajax', args=[self.project.pk]),
            {
                'code': 'АР-03',
                'name': 'План',
                'file': SimpleUploadedFile('plan.pdf', b'data', content_type='application/pdf'),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('Не выбран раздел', response.json()['error'])
