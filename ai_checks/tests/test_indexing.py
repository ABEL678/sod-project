"""Ф0.2: точечные тесты индексации InternalDocument (pypdfium2 + эмбеддинги)."""

from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas as rl_canvas

from ai_checks.adapters.embedding_onnx import BgeM3OnnxEmbedding
from ai_checks.domain.models import InternalChunk, InternalDocument
from ai_checks.domain.services import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    index_document,
    split_into_chunks,
)
from ai_checks.tests.base import MediaIsolationMixin
from ai_checks.tests.pdfgen import cyr_font
from ai_checks.tests.mocks import MockEmbeddingService
from storage.domain.models import (
    Company,
    Document,
    DocumentVersion,
    Project,
    Section,
)


def make_pdf(pages_text: list[str]) -> bytes:
    """Генерирует PDF через reportlab: по одному тексту на страницу.

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


class InternalDocumentIndexingTests(MediaIsolationMixin, TestCase):
    """index_document: чанки, векторы, страницы, идемпотентность."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='ai_tester', password='TestPass123',
        )
        self.company = Company.objects.create(name='ИИ-Тест')
        self.project = Project.objects.create(
            company=self.company,
            code='AI-1',
            name='Проект ИИ',
            status='active',
            created_by=self.user,
        )
        self.section = Section.objects.create(
            project=self.project,
            code='АР',
            name='Архитектура',
            order=1,
        )
        self.document = Document.objects.create(
            project=self.project,
            section=self.section,
            code='SRC-01',
            name='Источник знаний',
            doc_type='report',
            status='draft',
            created_by=self.user,
        )
        pdf_bytes = make_pdf([
            'Page one source text. ' * 60,
            'Page two source text. ' * 60,
        ])
        version = DocumentVersion.objects.create(
            document=self.document,
            version_number=1,
            file=ContentFile(pdf_bytes, name='source.pdf'),
            file_size=len(pdf_bytes),
            uploaded_by=self.user,
        )
        self.document.current_version = version
        self.document.save(update_fields=['current_version'])
        self.version = version
        self.internal_doc = InternalDocument.objects.create(
            tenant_scope=InternalDocument.TenantScope.COMPANY,
            company=self.company,
            project=None,  # 8.2: для company-scope project = NULL
            source_document=self.document,
            doc_type=InternalDocument.DocType.INTERNAL_STANDARD,
            title='Внутренний стандарт',
            created_by=self.user,
        )

    def test_index_creates_chunks_with_vectors(self):
        count = index_document(
            self.internal_doc,
            self.version.file.path,
            embedding_service=MockEmbeddingService(dim=1024),
        )
        self.assertGreater(count, 1)
        chunks = list(self.internal_doc.chunks.order_by('seq'))
        self.assertEqual(len(chunks), count)
        self.assertEqual([c.seq for c in chunks], list(range(count)))
        for chunk in chunks:
            self.assertTrue(chunk.text.strip())
            self.assertEqual(len(chunk.embedding), 1024)
            self.assertIsNotNone(chunk.page_from)
            self.assertLessEqual(chunk.page_from, chunk.page_to)
        self.internal_doc.refresh_from_db()
        self.assertIsNotNone(self.internal_doc.indexed_at)
        self.assertEqual(self.internal_doc.embedding_model, 'mock-embedding@1')

    def test_pages_cover_both_pdf_pages(self):
        index_document(
            self.internal_doc,
            self.version.file.path,
            embedding_service=MockEmbeddingService(dim=1024),
        )
        chunks = list(self.internal_doc.chunks.order_by('seq'))
        self.assertEqual(chunks[0].page_from, 1)
        self.assertEqual(chunks[-1].page_to, 2)
        joined = '\n'.join(c.text for c in chunks)
        self.assertIn('Page one source text.', joined)
        self.assertIn('Page two source text.', joined)

    def test_reindex_replaces_chunks(self):
        service = MockEmbeddingService(dim=1024)
        first_count = index_document(
            self.internal_doc, self.version.file.path, embedding_service=service,
        )
        first_ids = set(self.internal_doc.chunks.values_list('id', flat=True))
        second_count = index_document(
            self.internal_doc, self.version.file.path, embedding_service=service,
        )
        self.assertEqual(first_count, second_count)
        second_ids = set(self.internal_doc.chunks.values_list('id', flat=True))
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(self.internal_doc.chunks.count(), second_count)

    def test_default_adapter_is_bge_m3_stub(self):
        count = index_document(self.internal_doc, self.version.file.path)
        self.assertGreater(count, 0)
        chunk = self.internal_doc.chunks.order_by('seq').first()
        self.assertEqual(len(chunk.embedding), 1024)
        self.internal_doc.refresh_from_db()
        self.assertEqual(self.internal_doc.embedding_model, 'bge-m3-v1')

    def test_empty_pdf_returns_zero_chunks(self):
        empty_pdf = make_pdf([''])
        empty_version = DocumentVersion.objects.create(
            document=self.document,
            version_number=2,
            file=ContentFile(empty_pdf, name='empty.pdf'),
            file_size=len(empty_pdf),
            uploaded_by=self.user,
        )
        count = index_document(
            self.internal_doc,
            empty_version.file.path,
            embedding_service=MockEmbeddingService(dim=1024),
        )
        self.assertEqual(count, 0)
        self.assertEqual(self.internal_doc.chunks.count(), 0)
        self.internal_doc.refresh_from_db()
        self.assertIsNotNone(self.internal_doc.indexed_at)

    def test_embedding_dimension_mismatch_raises(self):
        class BrokenEmbedding:
            dim = 1024
            model_version = 'broken'

            def embed_texts(self, texts):
                return [[0.0] * 8 for _ in texts]

        with self.assertRaises(ValueError):
            index_document(
                self.internal_doc,
                self.version.file.path,
                embedding_service=BrokenEmbedding(),
            )

    def test_chunk_sequence_is_unique_per_document(self):
        index_document(
            self.internal_doc,
            self.version.file.path,
            embedding_service=MockEmbeddingService(dim=1024),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                InternalChunk.objects.create(
                    document=self.internal_doc,
                    seq=0,
                    text='дубль',
                    embedding=[0.0] * 1024,
                )


class SplitIntoChunksTests(TestCase):
    """Чистая функция нарезки: размер, перекрытие, страницы."""

    def test_chunk_size_and_overlap(self):
        pages = ['A' * 2500]
        chunks = split_into_chunks(pages)
        self.assertEqual(len(chunks), 3)  # 0-1000, 800-1800, 1600-2500
        self.assertEqual(len(chunks[0].text), CHUNK_SIZE)
        self.assertEqual(len(chunks[1].text), CHUNK_SIZE)
        self.assertEqual(len(chunks[2].text), 900)
        # перекрытие 200 символов между соседними чанками
        self.assertEqual(
            chunks[0].text[-CHUNK_OVERLAP:], chunks[1].text[:CHUNK_OVERLAP],
        )

    def test_page_mapping(self):
        chunks = split_into_chunks(['first page', 'second page'])
        self.assertEqual(chunks[0].page_from, 1)
        self.assertEqual(chunks[-1].page_to, 2)

    def test_blank_text_produces_no_chunks(self):
        self.assertEqual(split_into_chunks(['', '   ', '\n']), [])
