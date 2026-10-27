import tempfile
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from storage.domain.models import Project, Section, UserProfile
from storage.domain.services import upload_document


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

    def _upload(self, code='АР-01', name='План 1 этажа', content=b'data'):
        return upload_document(
            user=self.user_gip,
            project=self.project,
            section=self.section,
            code=code,
            name=name,
            doc_type='drawing',
            file=SimpleUploadedFile(f'{code}.pdf', content, content_type='application/pdf'),
        )

    def test_download_version(self):
        self.client.force_login(self.user_gip)
        version = self._upload(content=b'pdf-body')
        response = self.client.get(reverse('storage:document_download', args=[version.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertEqual(b''.join(response.streaming_content), b'pdf-body')

    def test_download_anonymous_allowed(self):
        version = self._upload()
        response = self.client.get(reverse('storage:document_download', args=[version.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'data')

    def test_download_without_read_permission_forbidden(self):
        version = self._upload()
        contractor = User.objects.create_user('contractor', password='pass')
        UserProfile.objects.create(user=contractor, role='contractor')
        self.client.force_login(contractor)
        response = self.client.get(reverse('storage:document_download', args=[version.pk]))
        self.assertEqual(response.status_code, 403)

    def test_export_vedomost(self):
        self.client.force_login(self.user_gip)
        version = self._upload()
        response = self.client.get(reverse('storage:export_vedomost', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('vedomost.xlsx', response['Content-Disposition'])

        data = b''.join(response.streaming_content)
        workbook = load_workbook(BytesIO(data))
        sheet = workbook.active
        self.assertEqual(sheet.cell(row=1, column=1).value, 'Шифр')
        self.assertEqual(sheet.cell(row=2, column=1).value, 'АР-01')
        self.assertEqual(sheet.cell(row=2, column=2).value, 'План 1 этажа')
        self.assertIsNotNone(sheet.cell(row=2, column=2).hyperlink)
        target = sheet.cell(row=2, column=2).hyperlink.target
        self.assertTrue(target.startswith('http://') or target.startswith('https://'))
        self.assertIn(f'/download/{version.pk}/', target)

    def test_export_anonymous_forbidden(self):
        response = self.client.get(reverse('storage:export_vedomost', args=[self.project.pk]))
        self.assertEqual(response.status_code, 403)

    def test_document_detail(self):
        self.client.force_login(self.user_gip)
        version = self._upload()
        response = self.client.get(reverse('storage:document_detail', args=[version.document_id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'storage/document_detail.html')
        self.assertContains(response, 'История версий')
        self.assertContains(response, 'Замечания')
        self.assertContains(response, 'Скачать')

    def test_document_detail_forbidden_without_rights(self):
        version = self._upload()
        contractor = User.objects.create_user('contractor2', password='pass')
        UserProfile.objects.create(user=contractor, role='contractor')
        self.client.force_login(contractor)
        response = self.client.get(reverse('storage:document_detail', args=[version.document_id]))
        self.assertEqual(response.status_code, 403)

    def test_project_detail_links_to_document_detail(self):
        version = self._upload()
        response = self.client.get(reverse('storage:project_detail', args=[self.project.pk]))
        self.assertContains(
            response,
            reverse('storage:document_detail', args=[version.document_id]),
        )
