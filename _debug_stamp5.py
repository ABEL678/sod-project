"""Глубокая диагностика: почему iter_line_segments не видит штамп."""

import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sod_core.settings')

import django
django.setup()

from ai_checks import pdfium_tools
from pathlib import Path

pdf_path = Path('media/documents/2026/10/ded677f8b5b246849c284a07ca7a41d3.pdf')

print(f"PDF: {pdf_path}\n")

with pdfium_tools.open_pdf(str(pdf_path)) as doc:
    view = pdfium_tools.PdfPageView(doc, 0)
    
    # Все объекты на странице
    print("--- Все объекты (все типы) ---")
    all_objs = list(view._page.get_objects())
    print(f"Всего объектов: {len(all_objs)}")
    for i, obj in enumerate(all_objs[:20]):
        obj_type = obj.type
        print(f"  [{i}] type={obj_type}")
        obj.close()
    
    # Только PATH объекты
    print(f"\n--- PATH объекты ---")
    from ai_checks.pdfium_tools import _PAGEOBJ_PATH
    path_objs = list(view._page.get_objects(filter=[_PAGEOBJ_PATH], max_depth=15))
    print(f"PATH объектов: {len(path_objs)}")
    for i, obj in enumerate(path_objs[:5]):
        seg_count = pdfium_tools.pdfium_raw.FPDFPath_CountSegments(obj)
        print(f"  [{i}] segments={seg_count}")
        obj.close()
    
    # Текстовые объекты
    print(f"\n--- Текстовые объекты ---")
    from ai_checks.pdfium_tools import _SEG_MOVETO, _SEG_LINETO
    text_objs = list(view._page.get_objects(filter=[pdfium_tools.pdfium_raw.FPDF_PAGEOBJ_TEXT], max_depth=15))
    print(f"TEXT объектов: {len(text_objs)}")
    
    # Image объекты
    print(f"\n--- Image объекты ---")
    img_objs = list(view._page.get_objects(filter=[pdfium_tools.pdfium_raw.FPDF_PAGEOBJ_IMAGE], max_depth=15))
    print(f"IMAGE объектов: {len(img_objs)}")
    
    # Проверим весь контент страницы
    print(f"\n--- Полный анализ объектов ---")
    all_obj_types = {}
    for obj in view._page.get_objects():
        t = obj.type
        all_obj_types[t] = all_obj_types.get(t, 0) + 1
        obj.close()
    print(f"Типы объектов: {all_obj_types}")
    
    view.close()
    doc.close()
