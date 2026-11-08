"""Этап 20 (треб. 1-5): индексация документов проекта (ProjectDocumentChunk).

Автоиндексация из upload_document (hook, треб. 4), статусы indexing_status
на DocumentVersion (треб. 3), резервная индексация reindex_project /
reindex_document (треб. 5). Тесты запускаются на PostgreSQL+pgvector
(docker-compose.dev.yml): проектный векторный поиск требует PG.
"""

from io import BytesIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas as rl_canvas

from ai_checks.domain.models import ProjectDocumentChunk
from ai_checks.domain.services import (
    index_project_document,
    reindex_document,
    reindex_project,
)
from ai_checks.tests.base import MediaIsolationMixin
from ai_checks.tests.mocks import MockEmbeddingService, mock_bge_adapter
from ai_checks.tests.pdfgen import cyr_font
from storage.domain.models import (
    Company,
    Document,
    DocumentVersion,
    Project,
    Section,
)
from storage.domain.services import upload_document


def make_pdf(pages_text: list[str]) -> bytes:
    """Генерирует PDF через reportlab (pypdfium2-стек, без PyMuPDF).

    Текст переносится внутри рамки (50, 50)-(545, 800) — как прежний
    insert_textbox PyMuPDF (длинный текст обрезался бы краем страницы).
    """
    font = cyr_font()
    buffer = BytesIO()
    canvas = rl_canvas.Canvas(buffer, pagesize=(595, 842))
    for text in pages_text:
        if text:
            canvas.setFont(font, 9)
            baseline = 50 + 9
            for line in simpleSplit(text, font, 9, 545 - 50):
                if baseline > 800:
                    break
                canvas.drawString(50, 842 - baseline, line)
                baseline += 9 * 1.2
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


@override_settings(AI_CHECKS_ENABLED=True)
class ProjectDocumentIndexingTests(MediaIsolationMixin, TestCase):
    """index_project_document: чанки, статусы, снимок раздела/папки."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username='proj_index_user', email='pi@example.com',
            password='TestPass123',
        )
        self.company = Company.objects.create(name='ПИ-Компания')
        self.project = Project.objects.create(
            company=self.company, code='PI-1', name='Проект индексации',
            status='active', created_by=self.user, gip=self.user,
        )
        self.section_ov = Section.objects.create(
            project=self.project, code='ОВ', name='Отопление и вентиляция',
            order=1,
        )
        self.embedding = MockEmbeddingService(dim=1024)
        # Eager-задачи upload_document идут без DI — мокаем строгий адаптер.
        mock_bge_adapter(self)

    def _upload_pdf(self, pages_text, code='ОВ-01', section=None):
        return upload_document(
            user=self.user,
            project=self.project,
            section=section or self.section_ov,
            code=code,
            name='Документ индексации',
            doc_type='report',
            file=SimpleUploadedFile(
                'doc.pdf', make_pdf(pages_text), 'application/pdf',
            ),
        )

    def test_upload_document_enqueues_indexing(self):
        """Треб. 4: upload_document при AI_CHECKS_ENABLED=True индексирует
        PDF (в eager-режиме Celery задача выполняется синхронно)."""
        version = self._upload_pdf(['Нагрузка на вентиляцию 120 м3/ч. ' * 40])
        version.refresh_from_db()
        self.assertEqual(version.indexing_status, 'done')
        self.assertGreater(
            ProjectDocumentChunk.objects.filter(
                document=version.document,
            ).count(), 0,
        )
        # Снимки раздела/папки (треб. 1).
        chunk = ProjectDocumentChunk.objects.filter(
            document=version.document,
        ).first()
        self.assertEqual(chunk.section_id, self.section_ov.pk)
        self.assertEqual(chunk.version_id, version.pk)

    def test_upload_non_pdf_skips_indexing(self):
        """Не-PDF файлы не ставятся в индексацию (статус остаётся pending)."""
        version = upload_document(
            user=self.user,
            project=self.project,
            section=self.section_ov,
            code='ОВ-02',
            name='Не PDF',
            doc_type='report',
            file=SimpleUploadedFile('data.txt', b'plain text', 'text/plain'),
        )
        version.refresh_from_db()
        self.assertEqual(version.indexing_status, 'pending')
        self.assertFalse(
            ProjectDocumentChunk.objects.filter(
                document=version.document,
            ).exists(),
        )

    def test_disabled_flag_skips_indexing(self):
        """AI_CHECKS_ENABLED=False (по умолчанию) — индексация не ставится."""
        with override_settings(AI_CHECKS_ENABLED=False):
            version = self._upload_pdf(['Текст страницы. ' * 40])
        version.refresh_from_db()
        self.assertEqual(version.indexing_status, 'pending')

    def test_index_replaces_old_chunks(self):
        """Повторная индексация заменяет прежние фрагменты (идемпотентно)."""
        version = self._upload_pdf(['Первый текст. ' * 40])
        first_count = ProjectDocumentChunk.objects.filter(
            document=version.document,
        ).count()
        new_version = upload_document(
            user=self.user,
            project=self.project,
            section=self.section_ov,
            code='ОВ-01',
            name='Документ индексации',
            doc_type='report',
            file=SimpleUploadedFile(
                'doc2.pdf', make_pdf(['Второй текст. ' * 40]),
                'application/pdf',
            ),
        )
        new_version.refresh_from_db()  # eager-задача уже отработала
        old_chunks = ProjectDocumentChunk.objects.filter(
            document=version.document,
            version=version,
        ).count()
        new_chunks = ProjectDocumentChunk.objects.filter(
            document=new_version.document,
            version=new_version,
        ).count()
        self.assertEqual(old_chunks, 0)
        self.assertEqual(new_chunks, first_count)
        self.assertEqual(new_version.indexing_status, 'done')

    def test_index_error_status_on_broken_pdf(self):
        """Битый PDF → статус error, задача не роняет Celery (треб. 3)."""
        version = DocumentVersion.objects.create(
            document=Document.objects.create(
                project=self.project,
                section=self.section_ov,
                code='ОВ-03',
                name='Битый',
                doc_type='report',
                status='draft',
                created_by=self.user,
            ),
            version_number=1,
            file=ContentFile(b'%PDF-1.4 broken', name='broken.pdf'),
            file_size=len(b'%PDF-1.4 broken'),
            uploaded_by=self.user,
        )
        self.document = version.document
        count = index_project_document(version.pk, self.embedding)
        version.refresh_from_db()
        self.assertEqual(count, 0)
        self.assertEqual(version.indexing_status, 'error')

    def test_missing_version_returns_zero(self):
        self.assertEqual(index_project_document(999999), 0)


@override_settings(AI_CHECKS_ENABLED=True)
class ReindexTests(MediaIsolationMixin, TestCase):
    """Резервная индексация: reindex_document / reindex_project (треб. 5)."""

    def setUp(self):
        # Мок ДО _make_document: upload уже запускает eager-индексацию.
        mock_bge_adapter(self)
        self.user = User.objects.create_superuser(
            username='reindex_user', email='ri@example.com',
            password='TestPass123',
        )
        self.other = User.objects.create_user(
            username='stranger', password='TestPass123',
        )
        self.company = Company.objects.create(name='Рекс-Компания')
        self.project = Project.objects.create(
            company=self.company, code='RX-1', name='Проект рекс',
            status='active', created_by=self.user, gip=self.user,
        )
        self.section = Section.objects.create(
            project=self.project, code='ЭОМ', name='Электрика', order=1,
        )
        self.doc_a = self._make_document('ЭОМ-01')
        self.doc_b = self._make_document('ЭОМ-02')

    def _make_document(self, code):
        """Загрузка PDF; возвращает ДОКУМЕНТ (upload_document → версия)."""
        version = upload_document(
            user=self.user,
            project=self.project,
            section=self.section,
            code=code,
            name=f'Документ {code}',
            doc_type='report',
            file=SimpleUploadedFile(
                'doc.pdf', make_pdf([f'Текст {code}. ' * 40]),
                'application/pdf',
            ),
        )
        return version.document

    def test_reindex_document(self):
        version = self.doc_a.current_version
        DocumentVersion.objects.filter(pk=version.pk).update(
            indexing_status='error',
        )
        with mock.patch(
            'ai_checks.tasks.index_project_document_task',
        ) as task:
            version_id = reindex_document(self.doc_a.pk, self.user)
        self.assertEqual(version_id, version.pk)
        task.delay.assert_called_once_with(version.pk)
        version.refresh_from_db()
        self.assertEqual(version.indexing_status, 'pending')

    def test_reindex_document_ignores_kb_code(self):
        """KB-* документы не переиндексируются как проектные (0 версий)."""
        kb_doc = upload_document(
            user=self.user,
            project=self.project,
            section=self.section,
            code='KB-TEST000001',
            name='Стандарт',
            doc_type='report',
            file=SimpleUploadedFile(
                'kb.pdf', make_pdf(['Стандарт. ' * 40]), 'application/pdf',
            ),
            skip_ai_index=True,
        )
        with mock.patch(
            'ai_checks.tasks.index_project_document_task',
        ) as task:
            self.assertEqual(
                reindex_document(kb_doc.document_id, self.user), 0,
            )
        task.delay.assert_not_called()

    def test_reindex_project_enqueues_all(self):
        """Все текущие версии получают pending и по задаче Celery.

        Задача патчится: проверяем постановку и статусы до выполнения
        (в eager-режиме задача синхронно доводила бы статус до done).
        """
        DocumentVersion.objects.update(indexing_status='error')
        with mock.patch(
            'ai_checks.tasks.index_project_document_task',
        ) as task:
            count = reindex_project(self.project.pk, self.user)
        self.assertEqual(count, 2)
        self.assertEqual(task.delay.call_count, 2)
        statuses = set(
            DocumentVersion.objects.filter(
                document__project=self.project,
            ).values_list('indexing_status', flat=True)
        )
        self.assertEqual(statuses, {'pending'})

    def test_reindex_project_no_access_denied(self):
        from django.core.exceptions import PermissionDenied

        with self.assertRaises(PermissionDenied):
            reindex_project(self.project.pk, self.other)

    def test_reindex_project_empty(self):
        empty_project = Project.objects.create(
            company=self.company, code='RX-2', name='Пустой',
            status='active', created_by=self.user,
        )
        self.assertEqual(reindex_project(empty_project.pk, self.user), 0)

    def test_eager_run_after_reindex(self):
        """Сквозной сценарий: reindex_project + eager-задачи → done."""
        DocumentVersion.objects.update(indexing_status='error')
        reindex_project(self.project.pk, self.user)
        statuses = set(
            DocumentVersion.objects.filter(
                document__project=self.project,
            ).values_list('indexing_status', flat=True)
        )
        self.assertEqual(statuses, {'done'})
