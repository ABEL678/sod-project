"""Проверка: есть ли геометрия в правом нижнем углу страницы."""

import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sod_core.settings')

import django
django.setup()

from ai_checks import pdfium_tools
from ctypes import c_float
from pathlib import Path

pdf_path = Path('media/documents/2026/10/ded677f8b5b246849c284a07ca7a41d3.pdf')

with pdfium_tools.open_pdf(str(pdf_path)) as doc:
    view = pdfium_tools.PdfPageView(doc, 0)
    
    # Все сегменты со всей страницы
    all_segments = list(view.iter_line_segments())
    print(f"Всего сегментов: {len(all_segments)}")
    
    # Разобьём по зонам
    zone_x0 = view.rect.width * 0.6
    zone_y0 = view.rect.height * 0.7
    
    # Сегменты в зоне штампа
    stamp_segments = []
    for s in all_segments:
        x0, y0, x1, y1 = s
        if (x0 >= zone_x0 - 10 and x1 >= zone_x0 - 10 and
            y0 >= zone_y0 - 10 and y1 >= zone_y0 - 10):
            stamp_segments.append(s)
    
    print(f"\nСегментов в зоне штампа (x>{zone_x0:.0f}, y>{zone_y0:.0f}): {len(stamp_segments)}")
    for s in stamp_segments[:10]:
        print(f"  {s}")
    
    # Все прямоугольники
    all_rects = list(view.iter_rects())
    print(f"\nВсего прямоугольников: {len(all_rects)}")
    for r in all_rects:
        in_zone = r.x0 >= zone_x0 - 10 and r.y0 >= zone_y0 - 10
        print(f"  {r} zone={in_zone}")
    
    # Проверим текст на всей странице
    print(f"\n--- Текст на странице (первые 500 символов) ---")
    full_text = view.get_text()
    print(f"Длина текста: {len(full_text)}")
    print(f"Первые 500: {repr(full_text[:500])}")
    
    # Текст в зоне штампа
    zone = pdfium_tools.Rect(zone_x0, zone_y0, view.rect.x1, view.rect.y1)
    stamp_text = view.get_text(clip=zone)
    print(f"\n--- Текст в зоне штампа ---")
    print(f"Длина: {len(stamp_text)}")
    print(f"Текст: {repr(stamp_text[:500])}")
    
    # Проверим: а есть ли текст в правом нижнем углу (шире зона)
    wide_zone = pdfium_tools.Rect(
        view.rect.width * 0.3,
        view.rect.height * 0.5,
        view.rect.x1,
        view.rect.y1,
    )
    wide_text = view.get_text(clip=wide_zone)
    print(f"\n--- Текст в широкой зоне (правый нижний квадрант) ---")
    print(f"Длина: {len(wide_text)}")
    print(f"Текст: {repr(wide_text[:500])}")
    
    view.close()
    doc.close()
