"""Ф1: точечные тесты детерминированных правил (domain/rules.py)."""

from unittest import skipUnless

from django.test import SimpleTestCase

from ai_checks import pdfium_tools
from ai_checks.tests.base import find_cyrillic_font
from ai_checks.tests.pdfgen import make_pdf_bytes

CYRILLIC_FONT = find_cyrillic_font()

from ai_checks.domain.rules import (
    MIN_FONT_PT,
    check_font,
    check_frame,
    check_stamp,
)


PAGE_W, PAGE_H = 595, 842  # A4 portrait, пункты


def make_page(rects=(), lines=(), texts=()):
    """Создаёт PDF из одной страницы и отдаёт (doc, view) для правил.

    Элементы задаются в TL-координатах PyMuPDF (см. tests/pdfgen.py);
    doc закрывается вызывающим тестом (doc.close()).
    """
    data = make_pdf_bytes(rects=rects, lines=lines, texts=texts)
    doc = pdfium_tools.open_pdf(data)
    return doc, pdfium_tools.PdfPageView(doc, 0)


class FrameRuleTests(SimpleTestCase):
    def test_frame_present_no_findings(self):
        doc, page = make_page(rects=[(20, 20, PAGE_W - 20, PAGE_H - 20)])
        self.assertEqual(check_frame(page), [])
        doc.close()

    def test_frame_missing_finding(self):
        doc, page = make_page()
        findings = check_frame(page)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding['page'], 1)
        self.assertEqual(finding['severity'], 'major')
        self.assertIn('рамка', finding['message'])
        # bbox = None: не выделяем красным весь лист (замечание владельца).
        self.assertIsNone(finding['bbox'])
        doc.close()


class StampRuleTests(SimpleTestCase):
    @skipUnless(CYRILLIC_FONT, 'Нужен шрифт с кириллицей для PDF')
    def test_stamp_by_legacy_text_no_findings(self):
        doc, page = make_page(
            texts=[('Основная надпись', 8, (400, 720))],
        )
        self.assertEqual(check_stamp(page), [])
        doc.close()

    @skipUnless(CYRILLIC_FONT, 'Нужен шрифт с кириллицей для PDF')
    def test_stamp_by_gost_labels_no_findings(self):
        # Реальный ГОСТ-штамп: «Основной надписи» в тексте нет — есть поля.
        doc, page = make_page(
            texts=[('Стадия Лист Листов', 8, (400, 720))],
        )
        self.assertEqual(check_stamp(page), [])
        doc.close()

    def test_stamp_by_table_lines_no_findings(self):
        lines = [
            (370, 600 + index * 12, 580, 600 + index * 12)
            for index in range(6)
        ] + [
            (380 + index * 40, 595, 380 + index * 40, 700)
            for index in range(5)
        ]

        doc, page = make_page(lines=lines)
        self.assertEqual(check_stamp(page), [])
        doc.close()

    def test_stamp_box_at_corner_no_findings(self):
        doc, page = make_page(rects=[(400, 780, 575, 825)])
        self.assertEqual(check_stamp(page), [])
        doc.close()

    def test_stamp_missing_finding(self):
        doc, page = make_page()
        findings = check_stamp(page)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['severity'], 'major')
        self.assertIn('основная надпись', findings[0]['message'].casefold())
        doc.close()


class FontRuleTests(SimpleTestCase):
    def test_normal_font_no_findings(self):
        doc, page = make_page(texts=[('Текст 10 pt', 10, (72, 100))])
        self.assertEqual(check_font(page), [])
        doc.close()

    @skipUnless(CYRILLIC_FONT, 'Нужен шрифт с кириллицей для PDF')
    def test_small_font_finding(self):
        doc, page = make_page(texts=[('Мелкий текст', 5, (72, 100))])
        findings = check_font(page)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding['severity'], 'minor')
        self.assertIn('Высота шрифта', finding['message'])
        self.assertIn('Мелкий', finding['evidence'].replace('\xa0', ' '))
        doc.close()

    def test_threshold_between_5pt_and_10pt(self):
        self.assertGreater(MIN_FONT_PT, 7.0)
        self.assertLess(MIN_FONT_PT, 7.2)
        doc, page = make_page(texts=[('Ровно 7 pt', 7, (72, 100))])
        self.assertEqual(len(check_font(page)), 1)
        doc.close()

    def test_stamp_zone_text_is_ignored(self):
        # Мелкий текст в зоне штампа правилом шрифта не проверяется.
        doc, page = make_page(texts=[('штамп 5pt', 5, (400, 720))])
        self.assertEqual(check_font(page), [])
        doc.close()
