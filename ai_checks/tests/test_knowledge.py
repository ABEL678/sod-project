"""База знаний: загрузка (UI-сервис), индексация, просмотр, удаление."""

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from ai_checks.domain.models import InternalDocument
from ai_checks.domain.services import (
    index_internal_document,
    knowledge_documents,
    remove_knowledge_document,
    upload_knowledge_document,
)
from storage.domain.models import (
    Company,
    Document,
    DocumentVersion,
    Project,
    Section,
)
from ai_checks.tests import pdfgen
from ai_checks.tests.base import MediaIsolationMixin
from ai_checks.tests.mocks import mock_bge_adapter


def make_pdf_bytes(text='Smoke sheet', font_size=10) -> bytes:
    """Одностраничный PDF с переносимым текстом (reportlab, как pdfgen)."""
    return pdfgen.make_pdf_pages(page_items=[{
        'wrapped_texts': [(text, font_size, (50, 50, 545, 800))],
    }])


@override_settings(AI_GIGACHAT_AUTH_KEY='')
class KnowledgeBaseServiceTests(MediaIsolationMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='kb_user', email='kb@example.com',
            password='TestPass123',
        )
        self.company = Company.objects.create(name='КБ-Компания')
        self.project = Project.objects.create(
            company=self.company, code='KB-1', name='Проект КБ',
            status='active', created_by=self.user, gip=self.user,
        )
        self.section = Section.objects.create(
            project=self.project, code='АР', name='Архитектура', order=1,
        )
        # Строгий bge-m3-адаптер → мок (реальная модель в тестах не нужна).
        mock_bge_adapter(self)

    def _upload(self, scope='project', doc_type='internal_standard'):
        return upload_knowledge_document(
            self.user,
            self.project,
            SimpleUploadedFile('kb.pdf', make_pdf_bytes(), 'application/pdf'),
            'Стандарт КБ',
            doc_type,
            scope,
        )

    def test_upload_creates_internal_document(self):
        internal = self._upload()
        self.assertEqual(internal.tenant_scope, 'project')
        self.assertEqual(internal.project_id, self.project.pk)
        self.assertIsNone(internal.company_id)
        self.assertEqual(internal.indexing_status, 'pending')
        self.assertTrue(internal.source_document.code.startswith('KB-'))
        self.assertEqual(internal.source_document.project_id, self.project.pk)

    def test_company_scope_upload(self):
        internal = self._upload(scope='company')
        self.assertEqual(internal.tenant_scope, 'company')
        self.assertEqual(internal.company_id, self.company.pk)
        self.assertIsNone(internal.project_id)

    def test_upload_rejects_unknown_type(self):
        with self.assertRaises(ValueError):
            self._upload(doc_type='wizard')

    def test_index_then_reindex(self):
        internal = self._upload()
        count = index_internal_document(internal.pk)
        internal.refresh_from_db()
        self.assertGreater(count, 0)
        self.assertEqual(internal.indexing_status, 'done')
        self.assertGreater(internal.chunks.count(), 0)
        self.assertIsNotNone(internal.indexed_at)
        self.assertEqual(index_internal_document(internal.pk), count)

    def test_index_failure_sets_status(self):
        internal = self._upload()
        version = internal.source_document.current_version
        version.file.save('broken.pdf', SimpleUploadedFile(
            'broken.pdf', b'%PDF-1.4 broken', 'application/pdf',
        ))
        index_internal_document(internal.pk)
        internal.refresh_from_db()
        self.assertEqual(internal.indexing_status, 'error')
        self.assertTrue(internal.index_error)

    def test_remove_is_soft(self):
        internal = self._upload()
        remove_knowledge_document(internal)
        internal.refresh_from_db()
        self.assertFalse(internal.is_active)
        self.assertNotIn(internal, list(knowledge_documents(self.project)))
        self.assertTrue(InternalDocument.objects.filter(pk=internal.pk).exists())

    def test_scope_isolation_other_company(self):
        other_company = Company.objects.create(name='Чужая КБ')
        other_project = Project.objects.create(
            company=other_company, code='KB-2', name='Чужой',
            status='active', created_by=self.user,
        )
        other_doc = Document.objects.create(
            project=other_project,
            section=Section.objects.create(
                project=other_project, code='АР', name='АР', order=1,
            ),
            code='OTHER', name='Other', doc_type='report',
            status='draft', created_by=self.user,
        )
        foreign = InternalDocument.objects.create(
            tenant_scope='company', company=other_company, project=None,
            source_document=other_doc, doc_type='other',
            title='Чужой документ', created_by=self.user,
        )
        self.assertNotIn(foreign, list(knowledge_documents(self.project)))


@override_settings(
    AI_CHECKS_ENABLED=True,
    AI_GIGACHAT_AUTH_KEY='',
)
class KnowledgeBaseViewTests(MediaIsolationMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='kb_gip', email='kbg@example.com',
            password='TestPass123',
        )
        self.company = Company.objects.create(name='КБ-Вью')
        self.project = Project.objects.create(
            company=self.company, code='KBV-1', name='Проект КБ вью',
            status='active', created_by=self.user, gip=self.user,
        )
        Section.objects.create(
            project=self.project, code='АР', name='АР', order=1,
        )
        # Celery eager: kb_upload индексирует сразу — мокаем адаптер.
        mock_bge_adapter(self)
        self.client.force_login(self.user)

    def test_kb_panel_renders(self):
        response = self.client.get(
            reverse('ai_checks:kb_panel', args=[self.project.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Загрузить документ')
        self.assertContains(response, 'kbTableWrap')

    def test_upload_via_view(self):
        response = self.client.post(
            reverse('ai_checks:kb_upload', args=[self.project.pk]),
            {
                'file': SimpleUploadedFile(
                    'kb.pdf', make_pdf_bytes(), 'application/pdf',
                ),
                'doc_type': 'internal_standard',
                'tenant_scope': 'project',
                'title': 'Стандарт через UI',
            },
        )
        self.assertEqual(response.status_code, 200)
        internal = InternalDocument.objects.get(title='Стандарт через UI')
        # Celery eager: индексация выполнилась синхронно.
        internal.refresh_from_db()
        self.assertEqual(internal.indexing_status, 'done')

    def test_chunks_fragment(self):
        internal = upload_knowledge_document(
            self.user, self.project,
            SimpleUploadedFile(
                'kb.pdf', make_pdf_bytes(), 'application/pdf',
            ),
            'Стандарт', 'internal_standard', 'project',
        )
        index_internal_document(internal.pk)
        response = self.client.get(reverse(
            'ai_checks:kb_chunks', args=[self.project.pk, internal.pk],
        ))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Фрагменты')

    def test_soft_delete_via_view(self):
        internal = upload_knowledge_document(
            self.user, self.project,
            SimpleUploadedFile(
                'kb.pdf', make_pdf_bytes(), 'application/pdf',
            ),
            'Стандарт', 'internal_standard', 'project',
        )
        response = self.client.post(reverse(
            'ai_checks:kb_disable', args=[self.project.pk, internal.pk],
        ))
        self.assertEqual(response.status_code, 200)
        internal.refresh_from_db()
        self.assertFalse(internal.is_active)

    def test_viewer_forbidden(self):
        viewer = User.objects.create_user(
            username='kb_viewer', password='TestPass123',
        )
        self.client.force_login(viewer)
        response = self.client.get(
            reverse('ai_checks:kb_panel', args=[self.project.pk]),
        )
        self.assertEqual(response.status_code, 404)
