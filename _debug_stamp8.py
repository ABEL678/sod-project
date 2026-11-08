"""Проверка: рекурсирует ли get_objects в XObjects."""

import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sod_core.settings')

import django
django.setup()

from ai_checks import pdfium_tools
import pypdfium2 as pdfium
from pathlib import Path

pdf_path = Path('media/documents/2026/10/ded677f8b5b246849c284a07ca7a41d3.pdf')

with pdfium.PdfDocument(str(pdf_path)) as doc:
    page = doc[0]
    
    # get_objects без фильтра — все типы
    all_objs = list(page.get_objects())
    print(f"get_objects() без фильтра: {len(all_objs)} объектов")
    
    # Проверим типы
    from collections import Counter
    types = Counter(obj.type for obj in all_objs)
    print(f"  Типы: {dict(types)}")
    
    # Type 1 = IMAGE, Type 2 = PATH, Type 3 = TEXT
    # Type 4 = FORM (XObject)
    form_objs = [obj for obj in all_objs if obj.type == 4]
    print(f"\nFORM (XObject) объектов: {len(form_objs)}")
    for i, obj in enumerate(form_objs[:5]):
        print(f"  [{i}] {obj}")
        obj.close()
    
    # Проверим max_depth
    print(f"\n--- get_objects с max_depth ---")
    for depth in [1, 5, 10, 15, 100]:
        objs = list(page.get_objects(max_depth=depth))
        print(f"  max_depth={depth}: {len(objs)} объектов")
    
    view = pdfium_tools.PdfPageView(doc, 0)
    
    # Проверим: есть ли PATH-объекты, которые попадают в зону штампа
    print(f"\n--- PATH-объекты с bbox в зоне штампа ---")
    from ctypes import c_float
    zone_x0 = view.rect.width * 0.6
    zone_y0 = view.rect.height * 0.7
    
    path_objs = list(page.get_objects(filter=[pdfium_tools._PAGEOBJ_PATH], max_depth=100))
    for i, obj in enumerate(path_objs):
        seg_count = pdfium_tools.pdfium_raw.FPDFPath_CountSegments(obj)
        if seg_count == 0:
            obj.close()
            continue
        
        # Соберем bbox
        all_x, all_y = [], []
        for j in range(seg_count):
            seg = pdfium_tools.pdfium_raw.FPDFPath_GetPathSegment(obj, j)
            x, y = c_float(), c_float()
            pdfium_tools.pdfium_raw.FPDFPathSegment_GetPoint(seg, x, y)
            all_x.append(x.value)
            all_y.append(y.value)
            pdfium_tools.pdfium_raw.FPDFPathSegment_GetClose(seg)
        
        if all_x:
            x_min, x_max = min(all_x), max(all_x)
            y_min, y_max = min(all_y), max(all_y)
            if x_min >= zone_x0 - 5 and y_min >= zone_y0 - 5:
                print(f"  PATH[{i}]: bbox=({x_min:.1f},{y_min:.1f})-({x_max:.1f},{y_max:.1f}) segs={seg_count}")
        
        obj.close()
    
    view.close()
