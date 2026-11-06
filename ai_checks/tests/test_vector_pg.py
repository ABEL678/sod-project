"""Ф0.2 (часть 2): PgVectorBackend.search — поведение и tenant-фильтры.

На SQLite (dev/тесты) search недоступен по контракту (NotImplementedError);
полный end-to-end поиск выполняется только на PostgreSQL (skip).
"""

from django.db import connection
from django.test import TestCase
from unittest import skipUnless

from ai_checks.adapters.vector_pg import PgVectorBackend, visibility_filter


class PgVectorBackendSqliteTests(TestCase):
    def test_search_raises_not_implemented_on_sqlite(self):
        if connection.vendor == 'postgresql':
            self.skipTest('Тест для не-PostgreSQL окружений')
        with self.assertRaises(NotImplementedError) as ctx:
            PgVectorBackend().search([0.0] * 1024, {}, 5)
        self.assertIn(
            'PgVectorBackend.search доступен только на PostgreSQL',
            str(ctx.exception),
        )


class VisibilityFilterTests(TestCase):
    """Правило выборки 8.2: global всем, company — своей, project — своему."""

    def test_without_tenant_context_only_global(self):
        query = str(visibility_filter({}))
        self.assertIn("'document__tenant_scope', 'global'", query)
        self.assertNotIn('company_id', query)
        self.assertNotIn('project_id', query)

    def test_company_context_includes_global_and_company(self):
        query = str(visibility_filter({'company_id': 7}))
        self.assertIn("'document__tenant_scope', 'global'", query)
        self.assertIn("'document__company_id', 7", query)
        self.assertNotIn('project_id', query)

    def test_project_context_includes_global_company_and_project(self):
        query = str(visibility_filter({'company_id': 7, 'project_id': 11}))
        self.assertIn("'document__company_id', 7", query)
        self.assertIn("'document__project_id', 11", query)

    def test_none_filters_are_fail_closed(self):
        self.assertEqual(str(visibility_filter(None)), str(visibility_filter({})))


@skipUnless(connection.vendor == 'postgresql',
            'Полный поиск PgVectorBackend доступен только на PostgreSQL')
class PgVectorBackendSearchTests(TestCase):
    """End-to-end: cosine-поиск по InternalChunk (только PostgreSQL)."""

    def test_search_returns_hits_with_scores(self):  # pragma: no cover - PG only
        from django.contrib.auth.models import User

        from ai_checks.domain.models import InternalChunk, InternalDocument
        from storage.domain.models import (
            Company, Document, Project, Section,
        )

        user = User.objects.create_user(username='pg_user', password='pgpass')
        company = Company.objects.create(name='PG Компания')
        project = Project.objects.create(
            company=company, code='PG-1', name='PG Проект',
            status='active', created_by=user,
        )
        section = Section.objects.create(
            project=project, code='АР', name='Архитектура', order=1,
        )
        document = Document.objects.create(
            project=project, section=section, code='PG-SRC', name='Источник',
            doc_type='report', status='draft', created_by=user,
        )
        internal = InternalDocument.objects.create(
            tenant_scope='company', company=company, project=None,
            source_document=document, doc_type='internal_standard',
            title='PG стандарт', created_by=user,
        )
        InternalChunk.objects.create(
            document=internal, seq=0, text='первый фрагмент',
            embedding=[1.0] + [0.0] * 1023,
        )
        InternalChunk.objects.create(
            document=internal, seq=1, text='второй фрагмент',
            embedding=[0.0, 1.0] + [0.0] * 1022,
        )
        hits = PgVectorBackend().search(
            [1.0] + [0.0] * 1023,
            {'company_id': company.pk},
            top_k=2,
        )
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]['text'], 'первый фрагмент')
        self.assertAlmostEqual(hits[0]['score'], 1.0, places=5)
        self.assertIn('id', hits[0])
        self.assertIn('document_id', hits[0])
        self.assertIn('page_from', hits[0])
        self.assertIn('page_to', hits[0])
