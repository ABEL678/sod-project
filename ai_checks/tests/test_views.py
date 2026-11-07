"""Ф1: тесты UI ИИ-контура (флаг, запуск, подтверждение/отклонение)."""

import pymupdf
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from ai_checks.domain.models import CheckRun, PendingCheckResult
from ai_checks.domain.services import run_rules_check
from ai_checks.tests.base import MediaIsolationMixin
from issues.models import Issue
from storage.domain.models import (
    Company,
    Document,
    DocumentVersion,
    Project,
    Section,
)


def make_bad_pdf() -> bytes:
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((72, 100), 'Текст', fontsize=5)
    data = pdf.tobytes()
    pdf.close()
    return data


def make_clean_pdf() -> bytes:
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.draw_rect(pymupdf.Rect(20, 20, 575, 822), color=(0, 0, 0), width=1)
    # Штамп, примыкающий к правому нижнему углу листа (новое правило).
    page.draw_rect(pymupdf.Rect(400, 780, 575, 825), color=(0, 0, 0), width=1)
    page.insert_text((72, 100), 'Текст', fontsize=10)
    data = pdf.tobytes()
    pdf.close()
    return data


class AiChecksUiTests(MediaIsolationMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='ai_ui', email='ui@example.com', password='TestPass123',
        )
        self.company = Company.objects.create(name='UI-Тест')
        self.project = Project.objects.create(
            company=self.company, code='UI-1', name='Проект UI',
            status='active', created_by=self.user,
        )
        self.section = Section.objects.create(
            project=self.project, code='АР', name='Архитектура', order=1,
        )
        self.document = Document.objects.create(
            project=self.project, section=self.section,
            code='UI-DOC', name='Лист UI', doc_type='drawing',
            status='draft', created_by=self.user,
        )
        pdf_bytes = make_bad_pdf()
        self.version = DocumentVersion.objects.create(
            document=self.document,
            version_number=1,
            file=ContentFile(pdf_bytes, name='ui.pdf'),
            file_size=len(pdf_bytes),
            uploaded_by=self.user,
        )
        self.document.current_version = self.version
        self.document.save(update_fields=['current_version'])
        self.client.force_login(self.user)

    def test_flag_off_returns_404(self):
        # Явно выключаем: дефолт может быть перекрыт .env на машине.
        with override_settings(AI_CHECKS_ENABLED=False):
            response = self.client.get(
                reverse('ai_checks:panel', args=[self.document.pk]),
            )
        self.assertEqual(response.status_code, 404)

    @override_settings(AI_CHECKS_ENABLED=True)
    def test_panel_before_run_offers_start(self):
        response = self.client.get(
            reverse('ai_checks:panel', args=[self.document.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Запустить проверку')

    @override_settings(AI_CHECKS_ENABLED=True)
    def test_start_runs_check_eagerly_and_shows_findings(self):
        response = self.client.post(
            reverse('ai_checks:start', args=[self.document.pk]),
        )
        self.assertEqual(response.status_code, 200)
        run = CheckRun.objects.get(document_version=self.version)
        self.assertEqual(run.status, CheckRun.Status.DONE)
        self.assertGreater(run.findings_total, 0)
        self.assertContains(response, 'Подтвердить')
        self.assertContains(response, 'Отклонить')
        self.assertContains(response, 'Лист 1')

    @override_settings(AI_CHECKS_ENABLED=True)
    def test_confirm_finding_creates_issue(self):
        run = run_rules_check(self.version, self.user)
        # Координаты хранятся в каноническом пространстве (rotation=0);
        # пересчёт под текущий поворот вьювера делает отрисовка.
        finding = (
            run.results.select_related('rule')
            .exclude(bbox__isnull=True)
            .first()
        )
        response = self.client.post(
            reverse('ai_checks:confirm', args=[finding.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['HX-Trigger'], 'ai-finding-confirmed',
        )
        finding.refresh_from_db()
        self.assertEqual(finding.status, PendingCheckResult.Status.CONFIRMED)
        self.assertEqual(finding.reviewed_by, self.user)
        issue = Issue.objects.get(document=self.document)
        self.assertTrue(issue.description.startswith('[ИИ]'))
        self.assertEqual(issue.page_number, finding.page)
        # Канонические ratios (неповёрнутый лист), упорядочены.
        self.assertIsNotNone(issue.x1_ratio)
        self.assertIsNotNone(issue.x2_ratio)
        self.assertLess(issue.x1_ratio, issue.x2_ratio)
        self.assertLess(issue.y1_ratio, issue.y2_ratio)
        # Скриншот (кроп bbox) приложен к Issue.
        self.assertTrue(issue.crop_image)
        self.assertIn('issue_crop_', issue.crop_image.name)

    @override_settings(AI_CHECKS_ENABLED=True)
    def test_reject_finding(self):
        run = run_rules_check(self.version, self.user)
        finding = run.results.first()
        response = self.client.post(
            reverse('ai_checks:reject', args=[finding.pk]),
        )
        self.assertEqual(response.status_code, 200)
        finding.refresh_from_db()
        self.assertEqual(finding.status, PendingCheckResult.Status.REJECTED)
        self.assertEqual(Issue.objects.count(), 0)

    @override_settings(AI_CHECKS_ENABLED=True)
    def test_preview_returns_png(self):
        run = run_rules_check(self.version, self.user)
        finding = run.results.first()
        response = self.client.get(
            reverse('ai_checks:preview', args=[finding.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertGreater(len(response.content), 100)

    @override_settings(AI_CHECKS_ENABLED=True)
    def test_clean_document_shows_no_findings(self):
        pdf_bytes = make_clean_pdf()
        version = DocumentVersion.objects.create(
            document=self.document,
            version_number=2,
            file=ContentFile(pdf_bytes, name='clean.pdf'),
            file_size=len(pdf_bytes),
            uploaded_by=self.user,
        )
        self.document.current_version = version
        self.document.save(update_fields=['current_version'])
        response = self.client.post(
            reverse('ai_checks:start', args=[self.document.pk]),
        )
        self.assertContains(response, 'Находок нет')
