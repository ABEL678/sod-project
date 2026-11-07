"""Генерация тестовых PDF через reportlab (замена PyMuPDF-фикстур).

Функции принимают координаты в системе PyMuPDF (TL-начало, y вниз от
верхнего края) и конвертируют в BL-систему reportlab — так фикстуры
сохраняют прежние координаты и правила проверяют те же места листа.
"""

from __future__ import annotations

from io import BytesIO

from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

from ai_checks.tests.base import find_cyrillic_font

_REGISTERED_FONT: str | None = None


def cyr_font() -> str:
    """TTF-шрифт с кириллицей (если найден) или Helvetica base14."""
    global _REGISTERED_FONT
    if _REGISTERED_FONT is None:
        path = find_cyrillic_font()
        if path:
            pdfmetrics.registerFont(TTFont('TestCyr', path))
            _REGISTERED_FONT = 'TestCyr'
        else:
            _REGISTERED_FONT = 'Helvetica'
    return _REGISTERED_FONT


def make_pdf_bytes(
    *,
    width: float = 595,
    height: float = 842,
    rects=(),
    lines=(),
    texts=(),
) -> bytes:
    """Собирает одностраничный PDF с элементами в TL-координатах PyMuPDF.

    rects: (x0, y0, x1, y1) — рамки/штампы;
    lines: (x0, y0, x1, y1) — линии;
    texts: (text, fontsize, (x, y_baseline)).
    """
    return make_pdf_pages(
        width=width,
        height=height,
        page_items=[{'rects': rects, 'lines': lines, 'texts': texts}],
    )


def make_pdf_pages(
    *,
    width: float = 595,
    height: float = 842,
    page_items: list[dict],
) -> bytes:
    """Многостраничный PDF: page_items — набор элементов на каждую страницу."""
    font = cyr_font()
    buffer = BytesIO()
    canvas = rl_canvas.Canvas(buffer, pagesize=(width, height))
    for items in page_items:
        for x0, y0, x1, y1 in items.get('rects', ()):
            canvas.rect(x0, height - y1, x1 - x0, y1 - y0, stroke=1, fill=0)
        for x0, y0, x1, y1 in items.get('lines', ()):
            canvas.line(x0, height - y0, x1, height - y1)
        for text, size, point in items.get('texts', ()):
            canvas.setFont(font, size)
            canvas.drawString(point[0], height - point[1], text)
        for text, size, box in items.get('wrapped_texts', ()):
            canvas.setFont(font, size)
            baseline = box[1] + size  # первая базовая линия (TL)
            for line in simpleSplit(text, font, size, box[2] - box[0]):
                if baseline > box[3]:
                    break
                canvas.drawString(box[0], height - baseline, line)
                baseline += size * 1.2
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()
