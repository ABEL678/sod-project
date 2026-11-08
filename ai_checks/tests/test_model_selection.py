"""Выбор LLM-модели в UI: параметр model доходит до LLMProvider.complete."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from ai_checks.domain.models import InternalChunk, InternalDocument
from ai_checks.domain.services import generate_gip_report, rag_answer
from ai_checks.ports.llm import LLMResponse
from ai_checks.tests.mocks import MockEmbeddingService, MockVectorBackend
from storage.domain.models import (
    Company,
    Document,
    DocumentVersion,
    Project,
    Section,
)


class SpyLLMProvider:
    """Запоминает переданные сообщения и model."""

    name = 'spy'
    model = 'spy-default'

    def __init__(self):
        self.calls = []

    def complete(self, messages, *, model=None, json_mode=False):
        self.calls.append({'messages': messages, 'model': model})
        return LLMResponse(
            text='Ответ выбранной модели',
            model=model or self.model,
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=3,
        )


class ModelSelectionServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='model_user', email='m@example.com',
            password='TestPass123',
        )
        self.company = Company.objects.create(name='Модель-Компания')
        self.project = Project.objects.create(
            company=self.company, code='MODEL-1', name='Проект модели',
            status='active', created_by=self.user, gip=self.user,
        )
        self.section = Section.objects.create(
            project=self.project, code='АР', name='Архитектура', order=1,
        )
        self.document = Document.objects.create(
            project=self.project, section=self.section,
            code='MODEL-DOC', name='Лист модели',
            doc_type='report', status='draft', created_by=self.user,
        )
        self.version = DocumentVersion.objects.create(
            document=self.document, version_number=1,
            file='documents/test/model.pdf', file_size=10,
            uploaded_by=self.user,
        )

    def test_gip_report_passes_model_to_llm(self):
        from issues.models import Issue

        Issue.objects.create(
            document=self.document, author=self.user,
            description='Замечание', status='NEW',
        )
        spy = SpyLLMProvider()
        today = timezone.localdate()  # локальная дата — см. _report_issues
        report = generate_gip_report(
            self.project, today - timedelta(days=1), today,
            self.user,
            llm_provider=spy,
            model='z-ai/glm-5.3-flash-free',
        )
        self.assertEqual(len(spy.calls), 1)
        self.assertEqual(spy.calls[0]['model'], 'z-ai/glm-5.3-flash-free')
        self.assertEqual(report.payload['summary'], 'Ответ выбранной модели')
        self.assertEqual(
            report.llm_call.model, 'z-ai/glm-5.3-flash-free',
        )

    def test_gip_report_default_model_is_none(self):
        today = timezone.localdate()  # локальная дата — см. _report_issues
        spy = SpyLLMProvider()
        generate_gip_report(
            self.project, today, today, self.user,
            llm_provider=spy,
        )
        self.assertIsNone(spy.calls[0]['model'])

    def test_rag_passes_model_to_llm(self):
        internal = InternalDocument.objects.create(
            tenant_scope='company', company=self.company, project=None,
            source_document=self.document, doc_type='internal_standard',
            title='Источник', created_by=self.user,
        )
        embedding = MockEmbeddingService(dim=1024)
        text = 'Минимальная ширина лифтовой шахты'
        InternalChunk.objects.create(
            document=internal, seq=0, text=text,
            embedding=embedding.embed_texts([text])[0],
        )
        spy = SpyLLMProvider()
        result = rag_answer(
            text,
            company_id=self.company.pk,
            project_id=self.project.pk,
            embedding_service=embedding,
            vector_backend=MockVectorBackend(),
            llm_provider=spy,
            model='deepseek/deepseek-v4.1-flash-free',
        )
        self.assertEqual(len(spy.calls), 1)
        self.assertEqual(
            spy.calls[0]['model'], 'deepseek/deepseek-v4.1-flash-free',
        )
        self.assertEqual(result['answer'], 'Ответ выбранной модели')
        self.assertTrue(result['sources'])


class ModelSelectionViewTests(TestCase):
    """Вьюхи: модель из POST валидируется по AI_AVAILABLE_MODELS."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username='view_model_user', email='vm@example.com',
            password='TestPass123',
        )
        self.company = Company.objects.create(name='Вью-Модель')
        self.project = Project.objects.create(
            company=self.company, code='VMODEL-1', name='Вью проект',
            status='active', created_by=self.user, gip=self.user,
        )
        self.section = Section.objects.create(
            project=self.project, code='АР', name='Архитектура', order=1,
        )
        self.document = Document.objects.create(
            project=self.project, section=self.section,
            code='VMODEL-DOC', name='Лист', doc_type='report',
            status='draft', created_by=self.user,
        )
        self.client.force_login(self.user)

    @override_settings(AI_CHECKS_ENABLED=True)
    def test_selected_model_is_whitelisted(self):
        from ai_checks.views import _selected_model

        class Request:
            def __init__(self, data):
                self.POST = data

        self.assertEqual(
            _selected_model(Request({'model': 'z-ai/glm-5.3-flash-free'})),
            'z-ai/glm-5.3-flash-free',
        )
        self.assertIsNone(_selected_model(Request({'model': 'evil/model'})))
        self.assertIsNone(_selected_model(Request({})))
