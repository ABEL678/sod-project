"""Синхронизация bbox ИИ-находок с поворотом листа (баг владельца).

PyMuPDF заменён на pypdfium2: координаты bbox — неповёрнутая система
(эмуляция в pdfium_tools); вьювер отображает лист с поворотом
(кнопка «Повернуть»). bbox_ratios_for_rotation переводит координаты
в ratios пространства отображения.
"""

from django.test import SimpleTestCase

from ai_checks.domain.services import bbox_ratios_for_rotation, crop_finding_image
from ai_checks.tests.pdfgen import make_pdf_bytes


PAGE_W, PAGE_H = 200, 300
BBOX = [10, 20, 30, 50]
PDF_PATH = None


def setUpModule():
    global PDF_PATH
    import tempfile

    handle, PDF_PATH = tempfile.mkstemp(suffix='.pdf')
    import os as _os
    _os.close(handle)  # Windows: освободить дескриптор до save
    with open(PDF_PATH, 'wb') as fh:
        fh.write(make_pdf_bytes(
            width=PAGE_W, height=PAGE_H,
            texts=[('Kilo', 10, (20, 40))],
        ))


def tearDownModule():
    import os

    if PDF_PATH and os.path.exists(PDF_PATH):
        os.remove(PDF_PATH)


class BboxRotationTests(SimpleTestCase):
    def test_rotation_0(self):
        ratios = bbox_ratios_for_rotation(PDF_PATH, 1, BBOX, 0)
        self.assertAlmostEqual(ratios['x1_ratio'], 10 / PAGE_W, places=6)
        self.assertAlmostEqual(ratios['x2_ratio'], 30 / PAGE_W, places=6)
        self.assertAlmostEqual(ratios['y1_ratio'], 20 / PAGE_H, places=6)
        self.assertAlmostEqual(ratios['y2_ratio'], 50 / PAGE_H, places=6)

    def test_rotation_90_swaps_axes(self):
        # 90° по часовой: x' = H - y, y' = x; отображение (H × W).
        ratios = bbox_ratios_for_rotation(PDF_PATH, 1, BBOX, 90)
        self.assertAlmostEqual(
            ratios['x1_ratio'], (PAGE_H - 50) / PAGE_H, places=6,
        )
        self.assertAlmostEqual(
            ratios['x2_ratio'], (PAGE_H - 20) / PAGE_H, places=6,
        )
        self.assertAlmostEqual(ratios['y1_ratio'], 10 / PAGE_W, places=6)
        self.assertAlmostEqual(ratios['y2_ratio'], 30 / PAGE_W, places=6)

    def test_rotation_180(self):
        ratios = bbox_ratios_for_rotation(PDF_PATH, 1, BBOX, 180)
        self.assertAlmostEqual(
            ratios['x1_ratio'], (PAGE_W - 30) / PAGE_W, places=6,
        )
        self.assertAlmostEqual(
            ratios['x2_ratio'], (PAGE_W - 10) / PAGE_W, places=6,
        )
        self.assertAlmostEqual(
            ratios['y1_ratio'], (PAGE_H - 50) / PAGE_H, places=6,
        )

    def test_rotation_270(self):
        ratios = bbox_ratios_for_rotation(PDF_PATH, 1, BBOX, 270)
        self.assertAlmostEqual(ratios['x1_ratio'], 20 / PAGE_H, places=6)
        self.assertAlmostEqual(ratios['x2_ratio'], 50 / PAGE_H, places=6)
        self.assertAlmostEqual(
            ratios['y1_ratio'], (PAGE_W - 30) / PAGE_W, places=6,
        )

    def test_rotation_normalized_mod_360(self):
        ratios_360 = bbox_ratios_for_rotation(PDF_PATH, 1, BBOX, 360)
        ratios_0 = bbox_ratios_for_rotation(PDF_PATH, 1, BBOX, 0)
        self.assertEqual(ratios_360, ratios_0)

    def test_missing_input_returns_empty(self):
        self.assertEqual(bbox_ratios_for_rotation(PDF_PATH, None, BBOX, 0), {})
        self.assertEqual(bbox_ratios_for_rotation(PDF_PATH, 1, None, 0), {})


class CropFindingImageTests(SimpleTestCase):
    def test_crop_returns_jpeg_base64(self):
        import base64

        encoded = crop_finding_image(PDF_PATH, 1, BBOX)
        self.assertIsNotNone(encoded)
        data = base64.b64decode(encoded)
        self.assertTrue(data.startswith(b'\xff\xd8'), 'ожидается JPEG')
        self.assertLess(len(data), 5 * 1024 * 1024)

    def test_crop_missing_page_returns_none(self):
        self.assertIsNone(crop_finding_image(PDF_PATH, 99, BBOX))
