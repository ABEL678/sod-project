"""Ф4: точечные тесты generate_gip_report (сводка ГИПу)."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from ai_checks.adapters.llm_http import StubLLMProvider
from ai_checks.domain.models import (
    CheckRun,
    GeneratedReport,
    LLMCallLog,
    PendingCheckResult,
    RuleDefinition,
)
from ai_checks.domain.services import generate_gip_report
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
        self.version = DocumentVersion.objects.create(
            document=self.document,
            version_number=1,
            file='documents/test/gip.pdf',
            file_size=100,
            uploaded_by=self.user,
        )
        self.check_run = CheckRun.objects.create(
            document_version=self.version,
            initiated_by=self.user,
            status=CheckRun.Status.DONE,
        )
        self.rule = RuleDefinition.objects.get(code='SPDS.RAME')

    def _finding(self, status, reviewed_at, message='Находка', severity=None):
        return PendingCheckResult.objects.create(
            check_run=self.check_run,
            rule=self.rule,
            status=status,
            severity=severity or self.rule.severity,
            page=1,
            bbox=[10, 10, 100, 50],
            message=message,
            reviewed_at=reviewed_at,
        )

    def test_report_aggregates_confirmed_findings(self):
        now = timezone.now()
        self._finding('confirmed', now, 'Нет рамки', 'major')
        self._finding('confirmed', now, 'Мелкий шрифт', 'minor')
        self._finding('pending', None, 'Не подтверждена', 'major')
        self._finding(
            'confirmed', now - timedelta(days=60), 'Старая', 'major',
        )

        report = generate_gip_report(
            self.project,
            (now - timedelta(days=30)).date(),
            now.date(),
            self.user,
            llm_provider=StubLLMProvider(),
        )

        self.assertIsInstance(report, GeneratedReport)
        payload = report.payload
        self.assertEqual(payload['total'], 2)
        self.assertEqual(payload['by_severity'], {'major': 1, 'minor': 1})
        self.assertEqual(payload['by_document'], {'GIP-DOC': 2})
        self.assertEqual(len(payload['top_issues']), 2)
        self.assertTrue(payload['summary'])
        self.assertIn('..', report.period)
        self.assertIsNotNone(report.llm_call)
        self.assertEqual(
            report.llm_call.purpose, LLMCallLog.Purpose.GIP_REPORT,
        )
        self.assertEqual(report.llm_call.status, LLMCallLog.Status.OK)
        self.assertEqual(report.created_by, self.user)

    def test_llm_error_still_produces_report(self):
        now = timezone.now()
        self._finding('confirmed', now, 'Нет рамки', 'major')

        class BrokenProvider:
            name = 'broken'

            def complete(self, messages, *, model=None, json_mode=False):
                raise RuntimeError('сеть недоступна')

        report = generate_gip_report(
            self.project, now.date(), now.date(),
            self.user, llm_provider=BrokenProvider(),
        )
        self.assertEqual(report.payload['total'], 1)
        self.assertEqual(
            report.llm_call.status, LLMCallLog.Status.ERROR,
        )
        self.assertIn('сеть недоступна', report.llm_call.error_message)
        self.assertTrue(report.payload['summary'])

    def test_no_findings_report(self):
        now = timezone.now()
        report = generate_gip_report(
            self.project, now.date(), now.date(),
            self.user, llm_provider=StubLLMProvider(),
        )
        self.assertEqual(report.payload['total'], 0)
        self.assertEqual(report.payload['by_severity'], {})
        self.assertEqual(report.payload['top_issues'], [])

    def test_invalid_period_raises(self):
        now = timezone.now()
        with self.assertRaises(ValueError):
            generate_gip_report(
                self.project, now.date(), (now - timedelta(days=1)).date(),
                self.user, llm_provider=StubLLMProvider(),
            )

    @override_settings(AI_LLM_API_KEY='')
    def test_stub_used_without_api_key(self):
        from ai_checks.adapters.llm_http import get_llm_provider
        self.assertIsInstance(get_llm_provider(), StubLLMProvider)
