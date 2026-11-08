"""Анализ PATH-объектов на наличие штампа."""

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
    
    # Все PATH объекты
    path_objs = list(view._page.get_objects(filter=[pdfium_tools._PAGEOBJ_PATH], max_depth=15))
    
    print(f"PATH объектов: {len(path_objs)}\n")
    
    for i, obj in enumerate(path_objs):
        seg_count = pdfium_tools.pdfium_raw.FPDFPath_CountSegments(obj)
        
        # Собираем все сегменты
        segments = []
        for j in range(seg_count):
            seg = pdfium_tools.pdfium_raw.FPDFPath_GetPathSegment(obj, j)
            x, y = c_float(), c_float()
            pdfium_tools.pdfium_raw.FPDFPathSegment_GetPoint(seg, x, y)
            seg_type = pdfium_tools.pdfium_raw.FPDFPathSegment_GetType(seg)
            is_close = pdfium_tools.pdfium_raw.FPDFPathSegment_GetClose(seg)
            segments.append((seg_type, is_close, x.value, y.value))
            pdfium_tools.pdfium_raw.FPDFPathSegment_GetClose(seg)  # cleanup
        
        # Найдем bbox всех сегментов
        all_x = [s[2] for s in segments]
        all_y = [s[3] for s in segments]
        if all_x and all_y:
            x_min, x_max = min(all_x), max(all_x)
            y_min, y_max = min(all_y), max(all_y)
            w = x_max - x_min
            h = y_max - y_min
            
            # Проверяем, попадает ли в зону штампа
            page_rect = view.rect
            zone_x0 = page_rect.x0 + page_rect.width * 0.6
            zone_y0 = page_rect.y0 + page_rect.height * 0.7
            
            in_stamp_zone = (x_min >= zone_x0 - 5 and y_min >= zone_y0 - 5)
            
            if in_stamp_zone or seg_count >= 10:
                print(f"PATH[{i}]: segments={seg_count}, bbox=({x_min:.1f},{y_min:.1f})-({x_max:.1f},{y_max:.1f}) w={w:.1f} h={h:.1f}, zone={in_stamp_zone}")
                # Покажем первые 5 сегментов
                for j, (st, cl, sx, sy) in enumerate(segments[:8]):
                    type_name = {1: 'MOVETO', 2: 'LINETO', 3: 'CLOSE'}.get(st, str(st))
                    print(f"    [{j}] {type_name} ({sx:.1f},{sy:.1f}) close={cl}")
        
        obj.close()
    
    view.close()
    doc.close()
