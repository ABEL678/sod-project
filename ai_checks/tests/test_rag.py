"""Ф2: точечные тесты RAG (rag_answer) — источники, изоляция тенантов.

Векторный поиск на SQLite имитируется MockVectorBackend (ORM + то же
правило видимости 8.2); эмбеддинги — детерминированный MockEmbeddingService.
"""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from ai_checks.domain.models import InternalChunk, InternalDocument
from ai_checks.domain.services import rag_answer
from ai_checks.tests.mocks import MockEmbeddingService, MockVectorBackend
from storage.domain.models import (
    Company,
    Document,
    Project,
    Section,
)


@override_settings(
    AI_GIGACHAT_AUTH_KEY='',
)
class RagAnswerTests(TestCase):
    DIM = 1024

    def setUp(self):
        self.user = User.objects.create_superuser(
            username='rag_user', email='rag@example.com',
            password='TestPass123',
        )
        self.company_a = Company.objects.create(name='Компания А')
        self.company_b = Company.objects.create(name='Компания Б')

        self.project_a = self._project(self.company_a, 'A-1', 'Проект А')
        self.project_b = self._project(self.company_b, 'B-1', 'Проект Б')

        self.embedding = MockEmbeddingService(dim=self.DIM)
        self.backend = MockVectorBackend()

        self.question = 'Какие минимальные габариты лифтовой шахты?'
        self.answer_text = self.question  # вектор совпадёт с вопросом

    def _project(self, company, code, name):
        project = Project.objects.create(
            company=company, code=code, name=name,
            status='active', created_by=self.user,
        )
        section = Section.objects.create(
            project=project, code='АР', name='Архитектура', order=1,
        )
        document = Document.objects.create(
            project=project, section=section,
            code=f'{code}-DOC', name=f'Источник {code}',
            doc_type='report', status='draft', created_by=self.user,
        )
        return project

    def _internal_doc(self, project, title, tenant_scope='company',
                      company='auto', doc_type='internal_standard',
                      is_active=True):
        document = Document.objects.filter(
            project=project, code__endswith='-DOC',
        ).first()
        if company == 'auto':
            company = None if tenant_scope == 'global' else project.company
        return InternalDocument.objects.create(
            tenant_scope=tenant_scope,
            company=company,
            project=project if tenant_scope == 'project' else None,
            source_document=document,
            doc_type=doc_type,
            title=title,
            is_active=is_active,
            created_by=self.user,
        )

    def _chunk(self, internal_doc, text, seq=0):
        return InternalChunk.objects.create(
            document=internal_doc,
            seq=seq,
            text=text,
            embedding=self.embedding.embed_texts([text])[0],
        )

    def test_rag_returns_matching_source(self):
        internal = self._internal_doc(self.project_a, 'СП по лифтам')
        chunk = self._chunk(internal, self.answer_text)

        result = rag_answer(
            self.question,
            company_id=self.company_a.pk,
            project_id=self.project_a.pk,
            embedding_service=self.embedding,
            vector_backend=self.backend,
        )

        self.assertNotEqual(result['answer'], 'Ничего не найдено')
        self.assertEqual(len(result['sources']), 1)
        source = result['sources'][0]
        self.assertEqual(source['chunk_id'], chunk.pk)
        self.assertEqual(source['document_title'], 'СП по лифтам')
        self.assertEqual(source['tenant_scope'], 'company')
        self.assertGreater(source['score'], 0.99)
        self.assertEqual(self.backend.calls[0]['filters'], {
            'company_id': self.company_a.pk,
            'project_id': self.project_a.pk,
        })

    def test_other_company_chunk_is_isolated(self):
        # Одинаковый текст в обеих компаниях → одинаковые векторы:
        # изоляция должна отсечь чужой чанк до ранжирования.
        internal_a = self._internal_doc(self.project_a, 'Источник А')
        chunk_a = self._chunk(internal_a, self.answer_text)
        internal_b = self._internal_doc(self.project_b, 'Источник Б')
        chunk_b = self._chunk(internal_b, self.answer_text)

        result = rag_answer(
            self.question,
            company_id=self.company_a.pk,
            project_id=None,
            embedding_service=self.embedding,
            vector_backend=self.backend,
        )

        returned_ids = {source['chunk_id'] for source in result['sources']}
        self.assertIn(chunk_a.pk, returned_ids)
        self.assertNotIn(chunk_b.pk, returned_ids)

    def test_empty_result(self):
        result = rag_answer(
            self.question,
            company_id=self.company_a.pk,
            project_id=self.project_a.pk,
            embedding_service=self.embedding,
            vector_backend=self.backend,
        )
        # UI-аудит (треб. 6.1): новая формулировка при отсутствии информации.
        self.assertEqual(
            result['answer'],
            'Нет информации в предоставленных источниках',
        )
        self.assertEqual(result['sources'], [])

    def test_project_scope_requires_project_context(self):
        internal = self._internal_doc(
            self.project_a, 'Проектное знание', tenant_scope='project',
        )
        chunk = self._chunk(internal, self.answer_text)

        without_project = rag_answer(
            self.question,
            company_id=self.company_a.pk,
            project_id=None,
            embedding_service=self.embedding,
            vector_backend=self.backend,
        )
        self.assertNotIn(
            chunk.pk,
            {source['chunk_id'] for source in without_project['sources']},
        )

        with_project = rag_answer(
            self.question,
            company_id=self.company_a.pk,
            project_id=self.project_a.pk,
            embedding_service=self.embedding,
            vector_backend=self.backend,
        )
        self.assertIn(
            chunk.pk,
            {source['chunk_id'] for source in with_project['sources']},
        )

    def test_global_scope_visible_to_all_companies(self):
        internal = self._internal_doc(
            self.project_a, 'Общий стандарт', tenant_scope='global',
        )
        chunk = self._chunk(internal, self.answer_text)
        result = rag_answer(
            self.question,
            company_id=self.company_b.pk,
            project_id=None,
            embedding_service=self.embedding,
            vector_backend=self.backend,
        )
        self.assertIn(
            chunk.pk,
            {source['chunk_id'] for source in result['sources']},
        )

    def test_inactive_document_excluded(self):
        internal = self._internal_doc(
            self.project_a, 'Отключённый', is_active=False,
        )
        chunk = self._chunk(internal, self.answer_text)
        result = rag_answer(
            self.question,
            company_id=self.company_a.pk,
            project_id=self.project_a.pk,
            embedding_service=self.embedding,
            vector_backend=self.backend,
        )
        self.assertNotIn(
            chunk.pk,
            {source['chunk_id'] for source in result['sources']},
        )

    def test_blank_question_raises(self):
        with self.assertRaises(ValueError):
            rag_answer(
                '   ',
                company_id=self.company_a.pk,
                embedding_service=self.embedding,
                vector_backend=self.backend,
            )


@override_settings(
    AI_GIGACHAT_AUTH_KEY='',
)
class RagAnswerStubLLMTests(TestCase):
    """RAG-ответ через StubLLMProvider (без API-ключа).

    Проверяет, что ответ — связный текст, а не склейка чанков.
    Быстрые тесты: без MockVectorBackend, проверяем только StubLLMProvider.
    """

    DIM = 1024

    def setUp(self):
        self.user = User.objects.create_superuser(
            username='rag_stub', email='stub@example.com',
            password='TestPass123',
        )
        self.company = Company.objects.create(name='Компания Тест')
        self.project = self._project(self.company, 'T-1', 'Проект Тест')
        self.embedding = MockEmbeddingService(dim=self.DIM)

        self.question = 'Какая общая нагрузка электроснабжения?'
        self.answer_text = (
            'Общая нагрузка электроснабжения составляет 250 кВт. '
            'Включает: освещение 50 кВт, розетки 150 кВт, '
            'специальное оборудование 50 кВт.'
        )

    def _project(self, company, code, name):
        project = Project.objects.create(
            company=company, code=code, name=name,
            status='active', created_by=self.user,
        )
        section = Section.objects.create(
            project=project, code='ЭОМ', name='Электроснабжение', order=1,
        )
        document = Document.objects.create(
            project=project, section=section,
            code=f'{code}-DOC', name=f'Источник {code}',
            doc_type='report', status='draft', created_by=self.user,
        )
        return project

    def _internal_doc(self, project, title, tenant_scope='company',
                      company='auto', doc_type='internal_standard',
                      is_active=True):
        document = Document.objects.filter(
            project=project, code__endswith='-DOC',
        ).first()
        if company == 'auto':
            company = None if tenant_scope == 'global' else project.company
        return InternalDocument.objects.create(
            tenant_scope=tenant_scope,
            company=company,
            project=project if tenant_scope == 'project' else None,
            source_document=document,
            doc_type=doc_type,
            title=title,
            is_active=is_active,
            created_by=self.user,
        )

    def test_stub_llm_formats_coherent_answer(self):
        """StubLLMProvider формирует связный ответ, а не склейку чанков."""
        from ai_checks.adapters.llm_http import StubLLMProvider
        from ai_checks.domain.services import LLM_ERROR_SUMMARY

        provider = StubLLMProvider()
        context_text = (
            '[1] ЭОМ-DOC (лист 5):\n'
            'Общая нагрузка электроснабжения составляет 250 кВт. '
            'Освещение 50 кВт, розетки 150 кВт, спец. оборудование 50 кВт.'
        )
        response = provider.complete([
            {'role': 'system', 'content': 'Ты помощник ГИПа.'},
            {'role': 'user',
             'content': f'Вопрос: {self.question}\n\nФрагменты документов:\n{context_text}'},
        ])

        # Ответ — связный текст, не [N] title:\ntext.
        self.assertNotIn('[1] ЭОМ-DOC', response.text[:30])
        self.assertIn('250', response.text)
        self.assertIn('кВт', response.text)

    def test_stub_llm_includes_source_reference(self):
        """Ответ содержит ссылку на источник (документ, лист)."""
        from ai_checks.adapters.llm_http import StubLLMProvider

        provider = StubLLMProvider()
        context_text = '[1] Расчёт нагрузок (лист 5):\n250 кВт.'
        response = provider.complete([
            {'role': 'system', 'content': 'Ты помощник.'},
            {'role': 'user',
             'content': f'Вопрос: Нагрузка?\n\nФрагменты:\n{context_text}'},
        ])

        self.assertIn('Расчёт нагрузок', response.text)

    def test_llm_error_summary_constant(self):
        """LLM_ERROR_SUMMARY определён и содержит правильный текст."""
        from ai_checks.domain.services import LLM_ERROR_SUMMARY

        self.assertEqual(LLM_ERROR_SUMMARY, 'Резюме недоступно (ошибка LLM)')
