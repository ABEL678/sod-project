"""Ф1: точечные тесты run_rules_check (CheckRun + PendingCheckResult)."""

import pymupdf
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase

from ai_checks.domain.models import (
    CheckRun,
    PendingCheckResult,
    RuleDefinition,
)
from ai_checks.domain.services import ENGINE_VERSION, run_rules_check
from ai_checks.tests.base import MediaIsolationMixin
from storage.domain.models import (
    Company,
    Document,
    DocumentVersion,
    Project,
    Section,
)


def make_pdf(*, with_frame=False, with_stamp=False, font_size=10) -> bytes:
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    if with_frame:
        page.draw_rect(pymupdf.Rect(20, 20, 575, 822), color=(0, 0, 0), width=1)
    if with_stamp:
        # Штамп, примыкающий к правому нижнему углу листа (новое правило).
        page.draw_rect(
            pymupdf.Rect(400, 780, 575, 825), color=(0, 0, 0), width=1,
        )
    page.insert_text((72, 100), 'Текст листа', fontsize=font_size)
    data = pdf.tobytes()
    pdf.close()
    return data


class RunRulesCheckTests(MediaIsolationMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='ai_rules', email='rules@example.com',
            password='TestPass123',
        )
        self.company = Company.objects.create(name='Правила-Тест')
        self.project = Project.objects.create(
            company=self.company, code='RULE-1', name='Проект правил',
            status='active', created_by=self.user,
        )
        self.section = Section.objects.create(
            project=self.project, code='АР', name='Архитектура', order=1,
        )
        self.document = Document.objects.create(
            project=self.project, section=self.section,
            code='RULE-DOC', name='Лист с ошибками',
            doc_type='drawing', status='draft', created_by=self.user,
        )

    def _version(self, pdf_bytes, number=1):
        version = DocumentVersion.objects.create(
            document=self.document,
            version_number=number,
            file=ContentFile(pdf_bytes, name=f'rule_{number}.pdf'),
            file_size=len(pdf_bytes),
            uploaded_by=self.user,
        )
        return version

    def test_seeded_rules_exist(self):
        codes = set(
            RuleDefinition.objects.filter(is_active=True)
            .values_list('code', flat=True)
        )
        self.assertTrue({'SPDS.RAME', 'SPDS.STAMP', 'SPDS.FONT'} <= codes)

    def test_bad_page_produces_pending_findings(self):
        version = self._version(make_pdf(font_size=5))
        run = run_rules_check(version, self.user)

        self.assertEqual(run.status, CheckRun.Status.DONE)
        self.assertEqual(run.engine_version, ENGINE_VERSION)
        self.assertGreaterEqual(run.rules_total, 3)
        findings = list(run.results.select_related('rule'))
        self.assertEqual(run.findings_total, len(findings))
        self.assertGreaterEqual(len(findings), 3)
        codes = {finding.rule.code for finding in findings}
        self.assertTrue({'SPDS.RAME', 'SPDS.STAMP', 'SPDS.FONT'} <= codes)
        for finding in findings:
            self.assertEqual(finding.status, PendingCheckResult.Status.PENDING)
            self.assertIsNotNone(finding.page)
            if finding.rule.code == 'SPDS.RAME':
                # Рамка: находка уровня страницы, bbox пуст (не выделяем
                # красным весь лист).
                self.assertIsNone(finding.bbox)
            else:
                self.assertIsNotNone(finding.bbox)
                self.assertEqual(len(finding.bbox), 4)
        severities = {
            finding.rule.code: finding.severity for finding in findings
        }
        self.assertEqual(severities['SPDS.RAME'], 'major')
        self.assertEqual(severities['SPDS.FONT'], 'minor')
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.finished_at)

    def test_good_page_has_no_findings(self):
        version = self._version(make_pdf(
            with_frame=True, with_stamp=True, font_size=10,
        ))
        run = run_rules_check(version, self.user)
        self.assertEqual(run.status, CheckRun.Status.DONE)
        self.assertEqual(run.findings_total, 0)
        self.assertEqual(run.results.count(), 0)

    def test_corrupt_pdf_marks_run_failed(self):
        version = self._version(b'%PDF-1.4 broken')
        run = run_rules_check(version, self.user)
        self.assertEqual(run.status, CheckRun.Status.FAILED)
        self.assertEqual(run.findings_total, 0)
        self.assertIn('error', run.meta)
        self.assertIsNotNone(run.finished_at)

    def test_inactive_rule_is_skipped(self):
        RuleDefinition.objects.filter(code='SPDS.FONT').update(is_active=False)
        version = self._version(make_pdf(font_size=5))
        run = run_rules_check(version, self.user)
        self.assertEqual(run.status, CheckRun.Status.DONE)
        codes = set(
            run.results.values_list('rule__code', flat=True)
        )
        self.assertNotIn('SPDS.FONT', codes)
