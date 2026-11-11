"""Этап 20 (треб. 6, 9, 10): сквозной RAG и история ИИ-поиска.

Поиск по ProjectDocumentChunk + InternalChunk с объединением и top_k,
формат источников, запись RagQuery, вьюхи истории (поиск/фильтры/
пагинация/детализация). Проектный поиск — через MockProjectVectorBackend
(ORM + косинус), внутренний — MockVectorBackend (как в test_rag.py).
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ai_checks.domain.models import RagQuery
from ai_checks.domain.services import rag_answer
from ai_checks.tests.mocks import (
    MockEmbeddingService,
    MockLLMProvider,
    MockProjectVectorBackend,
    MockVectorBackend,
)
from ai_checks.domain.models import InternalChunk, InternalDocument
from storage.domain.models import Company, Document, Project, Section


@override_settings(
    AI_GIGACHAT_AUTH_KEY='',
)
class CrossSourceRagTests(TestCase):
    """Треб. 6: объединение проектных чанков и базы знаний."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username='cross_user', email='cross@example.com',
            password='TestPass123',
        )
        self.company = Company.objects.create(name='Кросс-Компания')
        self.project = Project.objects.create(
            company=self.company, code='CR-1', name='Проект кросс',
            status='active', created_by=self.user, gip=self.user,
        )
        self.section_ov = Section.objects.create(
            project=self.project, code='ОВ', name='ОВ', order=1,
        )
        self.section_eom = Section.objects.create(
            project=self.project, code='ЭОМ', name='ЭОМ', order=2,
        )
        self.embedding = MockEmbeddingService(dim=1024)
        self.backend = MockVectorBackend()
        self.project_backend = MockProjectVectorBackend()
        self.question = 'Какая температура воздуха рабочая?'

    def _project_doc(self, code, section):
        return Document.objects.create(
            project=self.project, section=section,
            code=code, name=f'Док {code}',
            doc_type='report', status='draft', created_by=self.user,
        )

    def _internal_chunk(self, title, text):
        document = Document.objects.filter(
            project=self.project, code__endswith='X',
        ).first()
        if document is None:
            document = Document.objects.create(
                project=self.project, section=self.section_ov,
                code='CR-X', name='Для стандартов',
                doc_type='report', status='draft', created_by=self.user,
            )
        internal = InternalDocument.objects.create(
            tenant_scope='company', company=self.company,
            source_document=document, doc_type='internal_standard',
            title=title, created_by=self.user,
        )
        return InternalChunk.objects.create(
            document=internal, seq=0, text=text,
            embedding=self.embedding.embed_texts([text])[0],
        )

    def test_sources_format_and_merge(self):
        """Треб. 6: sources в формате {type, document_title, section_code,
        page, text, score}; проектные и стандартные источники сливаются."""
        doc = self._project_doc('ОВ-01', self.section_ov)
        ProjectChunkFactory.create(
            document=doc, section=self.section_ov,
            text=self.question, page=7, embedding_service=self.embedding,
        )
        internal = self._internal_chunk('СП по вентиляции', self.question)

        result = rag_answer(
            self.question,
            self.project.pk,
            self.user,
            embedding_service=self.embedding,
            vector_backend=self.backend,
            project_backend=self.project_backend,
        )

        types = {source['type'] for source in result['sources']}
        self.assertEqual(types, {'project', 'standard'})
        project_sources = [
            source for source in result['sources']
            if source['type'] == 'project'
        ]
        self.assertEqual(project_sources[0]['section_code'], 'ОВ')
        self.assertEqual(project_sources[0]['page'], 7)
        self.assertIn('ОВ-01', project_sources[0]['document_title'])
        standard_sources = [
            source for source in result['sources']
            if source['type'] == 'standard'
        ]
        self.assertEqual(standard_sources[0]['document_title'], 'СП по вентиляции')

    def test_section_filter_limits_project_chunks(self):
        """Треб. 8: section_code фильтрует проектные чанки по разделу."""
        doc_ov = self._project_doc('ОВ-02', self.section_ov)
        doc_eom = self._project_doc('ЭОМ-01', self.section_eom)
        ProjectChunkFactory.create(
            document=doc_ov, section=self.section_ov,
            text=self.question, embedding_service=self.embedding,
        )
        ProjectChunkFactory.create(
            document=doc_eom, section=self.section_eom,
            text=self.question, embedding_service=self.embedding,
        )
        result = rag_answer(
            self.question,
            self.project.pk,
            self.user,
            section_code='ОВ',
            embedding_service=self.embedding,
            vector_backend=self.backend,
            project_backend=self.project_backend,
        )
        project_sections = {
            source['section_code']
            for source in result['sources']
            if source['type'] == 'project'
        }
        self.assertEqual(project_sections, {'ОВ'})

    def test_source_type_project_only(self):
        """Треб. 8: source_type='project' — только проектные источники."""
        doc = self._project_doc('ОВ-03', self.section_ov)
        ProjectChunkFactory.create(
            document=doc, section=self.section_ov,
            text=self.question, embedding_service=self.embedding,
        )
        self._internal_chunk('Стандарт', self.question)
        result = rag_answer(
            self.question,
            self.project.pk,
            self.user,
            source_type='project',
            embedding_service=self.embedding,
            vector_backend=self.backend,
            project_backend=self.project_backend,
        )
        self.assertEqual(
            {source['type'] for source in result['sources']}, {'project'},
        )

    def test_rag_query_saved_with_user(self):
        """Треб. 6/9: запрос сохраняется в истории с пользователем."""
        doc = self._project_doc('ОВ-04', self.section_ov)
        ProjectChunkFactory.create(
            document=doc, section=self.section_ov,
            text=self.question, embedding_service=self.embedding,
        )
        rag_answer(
            self.question,
            self.project.pk,
            self.user,
            model='GigaChat-2-Max',
            embedding_service=self.embedding,
            vector_backend=self.backend,
            project_backend=self.project_backend,
            llm_provider=MockLLMProvider(),
        )
        query = RagQuery.objects.get(project=self.project)
        self.assertEqual(query.user, self.user)
        self.assertEqual(query.question, self.question)
        self.assertTrue(query.sources)
        self.assertEqual(query.model, 'GigaChat-2-Max')

    def test_rag_query_saved_on_empty_result(self):
        """Пустой результат тоже попадает в историю."""
        rag_answer(
            self.question,
            self.project.pk,
            self.user,
            embedding_service=self.embedding,
            vector_backend=self.backend,
            project_backend=self.project_backend,
        )
        query = RagQuery.objects.get(project=self.project)
        self.assertEqual(query.sources, [])
        # UI-аудит (треб. 6.1): новая формулировка при отсутствии информации.
        self.assertEqual(
            query.answer,
            'Нет информации в предоставленных источниках',
        )


class ProjectChunkFactory:
    """Хелпер создания ProjectDocumentChunk (общий для файлов тестов)."""

    @staticmethod
    def create(document, version=None, section=None, seq=0, text='',
               page=None, embedding_service=None):
        from django.core.files.base import ContentFile

        from ai_checks.domain.models import ProjectDocumentChunk
        from storage.domain.models import DocumentVersion as DV

        if embedding_service is None:
            from ai_checks.tests.mocks import MockEmbeddingService

            embedding_service = MockEmbeddingService(dim=1024)
        if version is None:
            version = document.current_version
            if version is None:
                version = DV.objects.create(
                    document=document, version_number=1,
                    file=ContentFile(b'', name='stub.pdf'), file_size=0,
                    uploaded_by=document.created_by,
                )
                document.current_version = version
                document.save(update_fields=['current_version'])
        return ProjectDocumentChunk.objects.create(
            document=document,
            version=version,
            section=section if section is not None else document.section,
            seq=seq,
            text=text,
            page_from=page,
            page_to=page,
            embedding=embedding_service.embed_texts([text])[0],
        )


@override_settings(
    AI_GIGACHAT_AUTH_KEY='',
    AI_CHECKS_ENABLED=True,
)
class RagHistoryViewTests(TestCase):
    """Треб. 9/10: история ИИ-поиска — видимость, поиск, фильтры, пагинация."""

    PAGE_SIZE = 20

    def setUp(self):
        self.user = User.objects.create_superuser(
            username='hist_user', email='hist@example.com',
            password='TestPass123',
        )
        self.member = User.objects.create_user(
            username='hist_member', password='TestPass123',
            first_name='Анна', last_name='Смирнова',
        )
        self.stranger = User.objects.create_user(
            username='hist_stranger', password='TestPass123',
        )
        self.company = Company.objects.create(name='Ист-Компания')
        self.project = Project.objects.create(
            company=self.company, code='HS-1', name='Проект истории',
            status='active', created_by=self.user, gip=self.user,
        )
        self.project.memberships.create(user=self.member, role_in_project='developer')
        # Доступ к проектам определяется компанией профиля
        # (get_accessible_projects), поэтому участнику нужна компания.
        self.member.userprofile.company = self.company
        self.member.userprofile.save()
        self.client = self.client_class()

    def _log_query(self, question, user, project=None, model='', days_ago=0):
        query = RagQuery.objects.create(
            user=user,
            project=project if project is not None else self.project,
            question=question,
            answer='Ответ',
            sources=[],
            model=model,
        )
        if days_ago:
            RagQuery.objects.filter(pk=query.pk).update(
                created_at=timezone.now() - timedelta(days=days_ago),
            )
            query.refresh_from_db()
        return query

    def _history_url(self, **params):
        return reverse('ai_checks:rag_history', args=[self.project.pk]) + (
            '?' + '&'.join(f'{k}={v}' for k, v in params.items()) if params else ''
        )

    def test_history_visible_to_members(self):
        """Треб. 9: история видна всем участникам проекта."""
        self._log_query('Вопрос ГИПа', self.user)
        self.client.force_login(self.member)
        response = self.client.get(self._history_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Вопрос ГИПа')

    def test_history_hidden_from_strangers(self):
        self._log_query('Вопрос ГИПа', self.user)
        self.client.force_login(self.stranger)
        response = self.client.get(self._history_url())
        self.assertEqual(response.status_code, 404)

    def test_history_search_by_question(self):
        """Треб. 10: поиск по вопросу."""
        self._log_query('Какие нагрузки из ОВ?', self.user)
        self._log_query('Другой вопрос', self.user)
        self.client.force_login(self.user)
        response = self.client.get(self._history_url(q='нагрузки'))
        self.assertContains(response, 'Какие нагрузки из ОВ?')
        self.assertNotContains(response, 'Другой вопрос')

    def test_history_filter_by_author(self):
        """Треб. 10: фильтр по автору."""
        self._log_query('Вопрос Анны', self.member)
        self._log_query('Вопрос ГИПа', self.user)
        self.client.force_login(self.user)
        response = self.client.get(self._history_url(author=self.member.pk))
        self.assertContains(response, 'Вопрос Анны')
        self.assertNotContains(response, 'Вопрос ГИПа')

    def test_history_filter_by_date(self):
        """Треб. 10: фильтр по дате (date_from)."""
        self._log_query('Старый запрос', self.user, days_ago=10)
        self._log_query('Свежий запрос', self.user, days_ago=1)
        self.client.force_login(self.user)
        today = timezone.localdate()
        response = self.client.get(
            self._history_url(
                date_from=(today - timedelta(days=5)).isoformat(),
            ),
        )
        self.assertContains(response, 'Свежий запрос')
        self.assertNotContains(response, 'Старый запрос')

    def test_history_pagination(self):
        """Треб. 10: пагинация по 20 записей на страницу."""
        for index in range(25):
            self._log_query(f'Вопрос номер {index}', self.user)
        self.client.force_login(self.user)
        first_page = self.client.get(self._history_url())
        self.assertEqual(len(first_page.context['page_obj'].object_list), 20)
        self.assertEqual(first_page.context['page_obj'].paginator.num_pages, 2)
        second_page = self.client.get(self._history_url(page=2))
        self.assertEqual(len(second_page.context['page_obj'].object_list), 5)

    def test_history_item_detail(self):
        """Треб. 9: клик по строке — ответ и источники."""
        query = self._log_query(
            'Вопрос с источником', self.user, model='GigaChat-2-Max',
        )
        RagQuery.objects.filter(pk=query.pk).update(
            sources=[{
                'type': 'project',
                'document_title': 'ОВ-05 — Отопление',
                'section_code': 'ОВ',
                'page': 3,
                'text': 'Цитата из документа',
                'score': 0.9,
            }],
        )
        self.client.force_login(self.member)
        response = self.client.get(
            reverse('ai_checks:rag_history_item', args=[self.project.pk, query.pk]),
        )
        self.assertContains(response, 'ОВ-05 — Отопление')
        self.assertContains(response, 'лист 3')
        self.assertContains(response, 'GigaChat-2-Max')
