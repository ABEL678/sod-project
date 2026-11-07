"""Отчёт ГИПу из замечаний (Issue) + экспорт PDF/Word."""

import io
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from ai_checks import pdfium_tools
from ai_checks.adapters.llm_http import StubLLMProvider
from ai_checks.domain.models import GeneratedReport, LLMCallLog
from ai_checks.domain.services import (
    LLM_ERROR_SUMMARY,
    generate_gip_report,
    gip_report_docx,
    gip_report_pdf,
)
from issues.models import Issue
from storage.domain.models import (
    Company,
    Document,
    DocumentVersion,
    Project,
    Section,
)


class GipReportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='gip_user', email='gip@example.com',
            password='TestPass123',
        )
        self.assignee = User.objects.create_user(
            username='dev_worker', password='TestPass123',
            first_name='Иван', last_name='Петров',
        )
        self.company = Company.objects.create(name='ГИП-Компания')
        self.project = Project.objects.create(
            company=self.company, code='GIP-1', name='Проект отчёта',
            status='active', created_by=self.user, gip=self.user,
        )
        self.section = Section.objects.create(
            project=self.project, code='АР', name='Архитектура', order=1,
        )
        self.document = Document.objects.create(
            project=self.project, section=self.section,
            code='GIP-DOC', name='Лист для отчёта',
            doc_type='drawing', status='draft', created_by=self.user,
        )
        DocumentVersion.objects.create(
            document=self.document, version_number=1,
            file='documents/test/gip.pdf', file_size=100,
            uploaded_by=self.user,
        )

    def _issue(self, status='NEW', due_date=None, assignee='auto',
               description='Замечание', age_days=0):
        issue = Issue.objects.create(
            document=self.document,
            author=self.user,
            assignee=self.assignee if assignee == 'auto' else assignee,
            description=description,
            status=status,
            due_date=due_date,
        )
        if age_days:
            Issue.objects.filter(pk=issue.pk).update(
                created_at=timezone.now() - timedelta(days=age_days),
            )
        return issue

    def test_report_aggregates_issues(self):
        now = timezone.now()
        yesterday = now.date() - timedelta(days=1)
        self._issue('NEW', due_date=yesterday, description='Просрочка')
        self._issue('IN_PROGRESS', description='В работе')
        self._issue('DONE', description='На проверке')
        self._issue('CLOSED', description='Закрыто')
        self._issue('CANCELLED', description='Аннулировано')
        self._issue('REJECTED', description='Не принято')
        self._issue('NEW', description='Старое', age_days=60)

        report = generate_gip_report(
            self.project,
            (now - timedelta(days=30)).date(),
            now.date(),
            self.user,
            llm_provider=StubLLMProvider(),
        )

        payload = report.payload
        self.assertEqual(payload['total_issues'], 6)
        self.assertEqual(payload['by_status'], {
            'open': 2, 'progress': 1, 'review': 1, 'closed': 2,
        })
        self.assertEqual(payload['overdue_count'], 1)
        self.assertEqual(payload['by_document'], {'GIP-DOC': 6})
        self.assertEqual(payload['by_assignee'], {'Иван Петров': 6})
        self.assertLessEqual(len(payload['top_issues']), 7)
        self.assertTrue(payload['summary'])
        self.assertIn('замечаний', payload['summary'].lower())
        self.assertNotIn('находк', payload['summary'].lower())
        self.assertEqual(
            report.llm_call.purpose, LLMCallLog.Purpose.GIP_REPORT,
        )
        self.assertEqual(report.llm_call.status, LLMCallLog.Status.OK)

    def test_llm_error_summary_text(self):
        self._issue('NEW')

        class BrokenProvider:
            name = 'broken'

            def complete(self, messages, *, model=None, json_mode=False):
                raise RuntimeError('нет сети')

        report = generate_gip_report(
            self.project,
            timezone.localdate(),
            timezone.localdate(),
            self.user,
            llm_provider=BrokenProvider(),
        )
        self.assertEqual(report.payload['summary'], LLM_ERROR_SUMMARY)
        self.assertEqual(report.payload['summary'],
                         'Резюме недоступно (ошибка LLM)')
        self.assertEqual(report.llm_call.status, LLMCallLog.Status.ERROR)

    def test_no_issues_report(self):
        report = generate_gip_report(
            self.project,
            timezone.localdate(),
            timezone.localdate(),
            self.user,
            llm_provider=StubLLMProvider(),
        )
        self.assertEqual(report.payload['total_issues'], 0)
        self.assertEqual(report.payload['overdue_count'], 0)
        self.assertEqual(report.payload['top_issues'], [])

    def test_invalid_period_raises(self):
        now = timezone.localdate()
        with self.assertRaises(ValueError):
            generate_gip_report(
                self.project, now, now - timedelta(days=1), self.user,
                llm_provider=StubLLMProvider(),
            )

    def _sample_report(self):
        self._issue('NEW', description='Просроченное замечание',
                    due_date=timezone.localdate() - timedelta(days=3))
        self._issue('IN_PROGRESS', description='В работе замечание')
        return generate_gip_report(
            self.project,
            (timezone.localdate() - timedelta(days=30)),
            timezone.localdate(),
            self.user,
            llm_provider=StubLLMProvider(),
        )

    def test_export_pdf(self):
        report = self._sample_report()
        data = gip_report_pdf(report)
        self.assertTrue(data.startswith(b'%PDF'))
        self.assertGreater(len(data), 1000)
        with pdfium_tools.open_pdf(io.BytesIO(data)) as pdf:
            pages = []
            for index in range(len(pdf)):
                view = pdfium_tools.PdfPageView(pdf, index)
                try:
                    pages.append(view.get_text())
                finally:
                    view.close()
            text = ''.join(pages)
        self.assertIn('Отчет ГИПа по проекту', text)
        self.assertIn('GIP-DOC', text)
        self.assertIn('Ответственный', text)
        self.assertIn('Новое', text)

    def test_export_docx(self):
        import io

        from docx import Document as DocxDocument

        report = self._sample_report()
        data = gip_report_docx(report)
        self.assertTrue(data.startswith(b'PK'))  # zip-контейнер docx
        document = DocxDocument(io.BytesIO(data))
        paragraphs = '\n'.join(
            paragraph.text for paragraph in document.paragraphs
        )
        tables_text = '\n'.join(
            cell.text for table in document.tables
            for row in table.rows for cell in row.cells
        )
        self.assertIn('Отчет ГИПа по проекту', paragraphs)
        self.assertIn('Резюме (LLM)', paragraphs)
        self.assertIn('GIP-DOC', tables_text)
        self.assertIn('Иван Петров', tables_text)
