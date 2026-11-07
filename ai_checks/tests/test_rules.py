"""Ф1: точечные тесты детерминированных правил (domain/rules.py)."""

import pymupdf
from unittest import skipUnless

from django.test import SimpleTestCase

from ai_checks.tests.base import find_cyrillic_font

CYRILLIC_FONT = find_cyrillic_font()

from ai_checks.domain.rules import (
    MIN_FONT_PT,
    check_font,
    check_frame,
    check_stamp,
)


PAGE_W, PAGE_H = 595, 842  # A4 portrait, пункты


def make_page(draw=None, texts=None):
    """Создаёт PDF из одной страницы и отдаёт объект page (doc живёт в тесте)."""
    pdf = pymupdf.open()
    page = pdf.new_page(width=PAGE_W, height=PAGE_H)
    if draw:
        draw(page)
    if CYRILLIC_FONT:
        page.insert_font(fontname='cyr', fontfile=CYRILLIC_FONT)
    for text, size, point in texts or []:
        page.insert_text(
            point, text, fontsize=size,
            fontname='cyr' if CYRILLIC_FONT else 'helv',
        )
    return pdf, page


class FrameRuleTests(SimpleTestCase):
    def test_frame_present_no_findings(self):
        pdf, page = make_page(
            draw=lambda p: p.draw_rect(
                pymupdf.Rect(20, 20, PAGE_W - 20, PAGE_H - 20),
                color=(0, 0, 0), width=1,
            ),
        )
        self.assertEqual(check_frame(page), [])
        pdf.close()

    def test_frame_missing_finding(self):
        pdf, page = make_page()
        findings = check_frame(page)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding['page'], 1)
        self.assertEqual(finding['severity'], 'major')
        self.assertIn('рамка', finding['message'])
        # bbox = None: не выделяем красным весь лист (замечание владельца).
        self.assertIsNone(finding['bbox'])
        pdf.close()


class StampRuleTests(SimpleTestCase):
    @skipUnless(CYRILLIC_FONT, 'Нужен шрифт с кириллицей для PDF')
    def test_stamp_by_legacy_text_no_findings(self):
        pdf, page = make_page(texts=[
            ('Основная надпись', 8, (400, 720)),
        ])
        self.assertEqual(check_stamp(page), [])
        pdf.close()

    @skipUnless(CYRILLIC_FONT, 'Нужен шрифт с кириллицей для PDF')
    def test_stamp_by_gost_labels_no_findings(self):
        # Реальный ГОСТ-штамп: «Основной надписи» в тексте нет — есть поля.
        pdf, page = make_page(texts=[
            ('Стадия Лист Листов', 8, (400, 720)),
        ])
        self.assertEqual(check_stamp(page), [])
        pdf.close()

    def test_stamp_by_table_lines_no_findings(self):
        def draw_table(p):
            for index in range(6):
                y = 600 + index * 12
                p.draw_line((370, y), (580, y), color=(0, 0, 0), width=0.5)
            for index in range(5):
                x = 380 + index * 40
                p.draw_line((x, 595), (x, 700), color=(0, 0, 0), width=0.5)

        pdf, page = make_page(draw=draw_table)
        self.assertEqual(check_stamp(page), [])
        pdf.close()

    def test_stamp_box_at_corner_no_findings(self):
        pdf, page = make_page(
            draw=lambda p: p.draw_rect(
                pymupdf.Rect(400, 780, 575, 825),
                color=(0, 0, 0), width=1,
            ),
        )
        self.assertEqual(check_stamp(page), [])
        pdf.close()

    def test_stamp_missing_finding(self):
        pdf, page = make_page()
        findings = check_stamp(page)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['severity'], 'major')
        self.assertIn('основная надпись', findings[0]['message'].casefold())
        pdf.close()


class FontRuleTests(SimpleTestCase):
    def test_normal_font_no_findings(self):
        pdf, page = make_page(texts=[('Текст 10 pt', 10, (72, 100))])
        self.assertEqual(check_font(page), [])
        pdf.close()

    @skipUnless(CYRILLIC_FONT, 'Нужен шрифт с кириллицей для PDF')
    def test_small_font_finding(self):
        pdf, page = make_page(texts=[('Мелкий текст', 5, (72, 100))])
        findings = check_font(page)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding['severity'], 'minor')
        self.assertIn('Высота шрифта', finding['message'])
        self.assertIn('Мелкий', finding['evidence'].replace('\xa0', ' '))
        pdf.close()

    def test_threshold_between_5pt_and_10pt(self):
        self.assertGreater(MIN_FONT_PT, 7.0)
        self.assertLess(MIN_FONT_PT, 7.2)
        pdf, page = make_page(texts=[('Ровно 7 pt', 7, (72, 100))])
        self.assertEqual(len(check_font(page)), 1)
        pdf.close()

    def test_stamp_zone_text_is_ignored(self):
        # Мелкий текст в зоне штампа правилом шрифта не проверяется.
        pdf, page = make_page(texts=[('штамп 5pt', 5, (400, 720))])
        self.assertEqual(check_font(page), [])
        pdf.close()
