"""Этап 20 (треб. 7-10, 13): вьюхи — База знаний, ИИ-поиск, история отчётов.

Тесты запускаются на PostgreSQL+pgvector: rag_search ищет по реальному
ProjectChunkVectorBackend (dev/prod-стек docker-compose.dev.yml).
"""

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from ai_checks.domain.models import GeneratedReport, RagQuery
from ai_checks.tests.base import MediaIsolationMixin
from ai_checks.tests.mocks import mock_bge_adapter
from ai_checks.tests.test_project_indexing import make_pdf
from issues.models import Issue
from storage.domain.models import Company, Project, Section


def _upload_pdf(user, project, section, code='ОВ-01'):
    """Загрузка PDF через сервис ядра (eager-задача индексирует)."""
    from storage.domain.services import upload_document

    return upload_document(
        user=user,
        project=project,
        section=section,
        code=code,
        name=f'Документ {code}',
        doc_type='report',
        file=SimpleUploadedFile(
            'doc.pdf', make_pdf([f'Нагрузка вентиляции {code}. ' * 40]),
            'application/pdf',
        ),
    )


@override_settings(
    AI_CHECKS_ENABLED=True,
    AI_LLM_PROVIDER='', AI_LLM_API_KEY='', AI_GIGACHAT_AUTH_KEY='',
)
class KnowledgeBaseViewTests(MediaIsolationMixin, TestCase):
    """Треб. 7: два блока вкладки «База знаний» + переиндексация (5)."""

    def setUp(self):
        self.gip = User.objects.create_superuser(
            username='kb_view_gip', email='kbg@example.com',
            password='TestPass123',
        )
        self.member = User.objects.create_user(
            username='kb_view_member', password='TestPass123',
        )
        self.company = Company.objects.create(name='KBV-Компания')
        self.project = Project.objects.create(
            company=self.company, code='KBV-1', name='Проект KBV',
            status='active', created_by=self.gip, gip=self.gip,
        )
        self.section = Section.objects.create(
            project=self.project, code='ОВ', name='ОВ', order=1,
        )
        # Eager-задачи upload_document идут без DI — мокаем строгий адаптер.
        mock_bge_adapter(self)
        self.client.force_login(self.gip)

    def test_kb_panel_has_both_blocks(self):
        _upload_pdf(self.gip, self.project, self.section)
        response = self.client.get(
            reverse('ai_checks:kb_panel', args=[self.project.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Документы проекта')
        self.assertContains(response, 'Внутренние стандарты')
        self.assertContains(response, 'Переиндексировать проект')
        self.assertContains(response, 'Загрузить стандарт')

    def test_kb_projects_table_lists_documents(self):
        """Треб. 7: таблица документов проекта с бейджем статуса."""
        version = _upload_pdf(self.gip, self.project, self.section)
        response = self.client.get(
            reverse('ai_checks:kb_projects_table', args=[self.project.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ОВ-01')
        self.assertContains(response, 'Готово')  # eager-задача → done
        self.assertContains(response, str(version.version_number))

    def test_kb_reindex_project_view(self):
        """Кнопка «Переиндексировать проект» ставит статусы и задачи."""
        from unittest import mock

        _upload_pdf(self.gip, self.project, self.section)
        with mock.patch('ai_checks.tasks.index_project_document_task'):
            response = self.client.post(
                reverse('ai_checks:kb_reindex_project', args=[self.project.pk]),
            )
        self.assertEqual(response.status_code, 200)

    def test_kb_reindex_project_forbidden_for_member(self):
        """База знаний доступна только ГИП/админ (треб. 7 — расширение 5)."""
        self.member.userprofile.company = self.company
        self.member.userprofile.save()
        self.client.force_login(self.member)
        response = self.client.post(
            reverse('ai_checks:kb_reindex_project', args=[self.project.pk]),
        )
        self.assertEqual(response.status_code, 404)

    def test_kb_reindex_document_view(self):
        from unittest import mock

        version = _upload_pdf(self.gip, self.project, self.section)
        with mock.patch('ai_checks.tasks.index_project_document_task'):
            response = self.client.post(
                reverse(
                    'ai_checks:kb_reindex_document',
                    args=[self.project.pk, version.document_id],
                ),
            )
        self.assertEqual(response.status_code, 200)
        version.refresh_from_db()
        self.assertEqual(version.indexing_status, 'pending')


@override_settings(
    AI_CHECKS_ENABLED=True,
    AI_LLM_PROVIDER='', AI_LLM_API_KEY='', AI_GIGACHAT_AUTH_KEY='',
)
class RagSearchViewTests(MediaIsolationMixin, TestCase):
    """Треб. 8: форма ИИ-поиска с фильтрами и чип-примером."""

    def setUp(self):
        self.gip = User.objects.create_superuser(
            username='rag_view_gip', email='rgv@example.com',
            password='TestPass123',
        )
        self.company = Company.objects.create(name='RGV-Компания')
        self.project = Project.objects.create(
            company=self.company, code='RGV-1', name='Проект RGV',
            status='active', created_by=self.gip, gip=self.gip,
        )
        self.section_ov = Section.objects.create(
            project=self.project, code='ОВ', name='ОВ', order=1,
        )
        self.client.force_login(self.gip)
        self.url = reverse('ai_checks:rag_search', args=[self.project.pk])

    def test_rag_panel_renders_filters_and_chip(self):
        response = self.client.get(
            reverse('ai_checks:rag_panel', args=[self.project.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Раздел')
        self.assertContains(response, 'Тип источника')
        self.assertContains(response, 'Какие нагрузки из ОВ учтены в ЭОМ?')
        self.assertContains(response, 'История запросов')

    def test_rag_search_finds_project_document(self):
        """Сквозной поиск: PDF проиндексирован, ответ содержит источник."""
        _upload_pdf(self.gip, self.project, self.section_ov)
        response = self.client.post(self.url, {
            'question': 'Какие нагрузки из ОВ учтены в ЭОМ?',
            'section_code': 'ОВ',
            'source_type': 'project',
            'model': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Документы проекта')
        self.assertContains(response, 'Раздел ОВ')
        self.assertTrue(
            RagQuery.objects.filter(project=self.project).exists(),
        )

    def test_rag_search_history_oob_refresh(self):
        """Треб. 9: после поиска история обновляется out-of-band."""
        response = self.client.post(self.url, {
            'question': 'Пустой поиск — нет документов',
            'section_code': '',
            'source_type': '',
            'model': '',
        })
        self.assertEqual(response.status_code, 200)
        # OOB-блок истории в ответе.
        self.assertContains(response, 'hx-swap-oob')


@override_settings(
    AI_CHECKS_ENABLED=True,
    AI_LLM_PROVIDER='', AI_LLM_API_KEY='', AI_GIGACHAT_AUTH_KEY='',
)
class ReportHistoryViewTests(MediaIsolationMixin, TestCase):
    """Треб. 13: история отчётов ГИПу под формой."""

    def setUp(self):
        self.gip = User.objects.create_superuser(
            username='rep_view_gip', email='rvg@example.com',
            password='TestPass123',
        )
        self.company = Company.objects.create(name='RVG-Компания')
        self.project = Project.objects.create(
            company=self.company, code='RVG-1', name='Проект RVG',
            status='active', created_by=self.gip, gip=self.gip,
        )
        self.section = Section.objects.create(
            project=self.project, code='АР', name='АР', order=1,
        )
        from storage.domain.models import Document

        self.document = Document.objects.create(
            project=self.project, section=self.section,
            code='АР-01', name='Лист АР',
            doc_type='drawing', status='draft', created_by=self.gip,
        )
        self.client.force_login(self.gip)

    def test_report_panel_lists_history(self):
        from datetime import date

        GeneratedReport.objects.create(
            project=self.project,
            period=f'{date(2026, 1, 1).isoformat()}..{date(2026, 1, 31).isoformat()}',
            payload={'total_issues': 0, 'summary': 'Пусто'},
            created_by=self.gip,
        )
        response = self.client.get(
            reverse('ai_checks:report_panel', args=[self.project.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'История отчётов')
        self.assertContains(response, '/pdf/')

    def test_report_generate_updates_history_oob(self):
        """После генерации отчёт попадает в историю (OOB-обновление)."""
        from datetime import date, timedelta

        Issue.objects.create(
            document=self.document, author=self.gip,
            description='Замечание для отчёта', status='NEW',
        )
        today = timezone_localdate()
        response = self.client.post(
            reverse('ai_checks:report_generate', args=[self.project.pk]),
            {
                'period_start': (today - timedelta(days=7)).isoformat(),
                'period_end': today.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'hx-swap-oob')
        self.assertContains(response, 'aiReportHistory')
        self.assertEqual(
            GeneratedReport.objects.filter(project=self.project).count(), 1,
        )


def timezone_localdate():
    from django.utils import timezone

    return timezone.localdate()
