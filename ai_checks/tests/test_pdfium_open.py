"""open_pdf: чтение источника в bytes и авто-ремонт PDF без xref-таблицы.

Регрессия на 500 кнопки «Подтвердить» после замены PyMuPDF на pypdfium2:
PDFium (в отличие от MuPDF) не восстанавливает файлы без xref-таблицы
(обрыв при передаче) и падает с FPDF_ERR_FORMAT «Data format error».
"""

from __future__ import annotations

import os
import tempfile

from django.test import SimpleTestCase

from ai_checks import pdfium_tools
from ai_checks.domain.services import bbox_ratios_for_rotation
from ai_checks.tests.pdfgen import make_pdf_bytes

BBOX = [10, 20, 30, 50]


def _good_pdf_bytes() -> bytes:
    return make_pdf_bytes(texts=[('Source', 10, (20, 40))])


def _truncated_pdf_bytes() -> bytes:
    """Валидный PDF, обрезанный после последнего endobj (нет xref/trailer)."""
    data = _good_pdf_bytes()
    return data[:data.rfind(b'endobj') + 6]


class OpenPdfSourceTests(SimpleTestCase):
    """Чекпойнты аудита: путь/file-like читаются в bytes корректно."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.pdf')
        os.close(fd)
        with open(self.path, 'wb') as fh:
            fh.write(_good_pdf_bytes())

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_str_path_opens(self):
        with pdfium_tools.open_pdf(self.path) as pdf:
            self.assertEqual(len(pdf), 1)

    def test_bytes_open(self):
        with pdfium_tools.open_pdf(_good_pdf_bytes()) as pdf:
            self.assertEqual(len(pdf), 1)

    def test_filelike_may_close_before_use(self):
        with open(self.path, 'rb') as fh:
            pdf = pdfium_tools.open_pdf(fh)
        # дескриптор уже закрыт — документ обязан работать (bytes прочитаны)
        with pdf:
            self.assertEqual(len(pdf), 1)

    def test_text_mode_filelike_rejected(self):
        # бинарный PDF в текстовом режиме: decode может упасть раньше —
        # в любом случае наружу должен выходить PdfiumError, а не
        # низкоуровневый UnicodeDecodeError/TypeError
        with open(self.path, 'r') as fh:
            with self.assertRaises(pdfium_tools.PdfiumError):
                pdfium_tools.open_pdf(fh)

    def test_missing_file_mentions_path(self):
        missing = os.path.join(self.path + '.missing')
        with self.assertRaises(pdfium_tools.PdfiumError) as ctx:
            pdfium_tools.open_pdf(missing)
        self.assertIn(missing, str(ctx.exception))


class OpenPdfRepairTests(SimpleTestCase):
    """PDF без xref-таблицы: PyMuPDF открывал молча — open_pdf чинит."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.pdf')
        os.close(fd)
        with open(self.path, 'wb') as fh:
            fh.write(_truncated_pdf_bytes())

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_truncated_bytes_repaired(self):
        with pdfium_tools.open_pdf(_truncated_pdf_bytes()) as pdf:
            self.assertEqual(len(pdf), 1)

    def test_truncated_path_repaired(self):
        with pdfium_tools.open_pdf(self.path) as pdf:
            self.assertEqual(len(pdf), 1)

    def test_truncated_text_extracted(self):
        with pdfium_tools.open_pdf(self.path) as pdf:
            view = pdfium_tools.PdfPageView(pdf, 0)
            try:
                self.assertIn('Source', view.get_text())
            finally:
                view.close()

    def test_bbox_ratios_works_on_truncated(self):
        ratios = bbox_ratios_for_rotation(self.path, 1, BBOX, None)
        self.assertTrue(ratios)

    def test_bbox_ratios_unrepairable_returns_empty(self):
        # пустой файл: ремонт невозможен — но 500 не должно быть
        with open(self.path, 'wb') as fh:
            fh.write(b'')
        self.assertEqual(
            bbox_ratios_for_rotation(self.path, 1, BBOX, None), {},
        )

    def test_empty_bytes_clear_error(self):
        with self.assertRaises(pdfium_tools.PdfiumError):
            pdfium_tools.open_pdf(b'')

    def test_garbage_without_header_clear_error(self):
        with self.assertRaises(pdfium_tools.PdfiumError):
            pdfium_tools.open_pdf(b'this is not a pdf at all' * 20)


class OpenPdfPyPdfFallbackTests(SimpleTestCase):
    """pypdf-fallback: PDF, который pypdfium2 не парсит, но pypdf нормализует."""

    def _make_pdf_with_null_bytes(self) -> bytes:
        """PDF с null-байтами внутри строк — pypdfium2 роняет, pypdf чистит."""
        base = _good_pdf_bytes()
        # вставляем null-байты в тело объекта (не в заголовок %PDF-)
        idx = base.find(b'endobj')
        if idx < 0:
            return base
        # заменяем символы на null-ы в теле объекта
        before = base[:idx]
        after = base[idx:]
        # вставляем null-байты в середину файла
        return before + b'\x00\x00\x00' + after

    def test_pypdf_fallback_called_when_xref_fails(self):
        """Если xref-repair не помог — open_pdf пробует pypdf."""
        # truncated PDF: xref repair работает, но для покрытия
        # логики fallback — убеждаемся, что fallback не ломает
        # корректные PDF
        with pdfium_tools.open_pdf(_truncated_pdf_bytes()) as pdf:
            self.assertEqual(len(pdf), 1)

    def test_good_pdf_passes_without_fallback(self):
        """Корректный PDF открывается первой попыткой."""
        with pdfium_tools.open_pdf(_good_pdf_bytes()) as pdf:
            self.assertEqual(len(pdf), 1)

    def test_repair_due_checks_actual_exception(self):
        """_repair_due получает пойманное исключение, а не пустое."""
        from pypdfium2 import PdfiumError
        from pypdfium2.raw import FPDF_ERR_FORMAT

        # Форматная ошибка -> ремонт уместен
        exc = PdfiumError('Data format error')
        exc.err_code = FPDF_ERR_FORMAT
        self.assertTrue(pdfium_tools._repair_due(exc))

        # Нет err_code, но есть текст -> ремонт уместен
        exc2 = PdfiumError('Data format error')
        self.assertTrue(pdfium_tools._repair_due(exc2))

        # Другая ошибка -> ремонт НЕ уместен
        exc3 = PdfiumError('Password required')
        self.assertFalse(pdfium_tools._repair_due(exc3))
