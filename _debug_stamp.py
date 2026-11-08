"""Диагностика координатных систем для SPDS.STAMP на pypdfium2."""

import sys
from ai_checks import pdfium_tools

# Откроем PDF из тестовых данных
import django
django.setup()

from ai_checks.domain.services import extract_pages

# Найдем реальный PDF
from storage.domain.models import Document
from storage.domain.services import get_accessible_documents
from django.contrib.auth.models import User

# Попробуем любой PDF-файл
import os
from pathlib import Path

# Ищем любой .pdf в media
media_root = Path('media')
pdf_files = list(media_root.rglob('*.pdf'))[:5]

if not pdf_files:
    print("Нет PDF-файлов в media. Ищем в тестовых данных...")
    # Используем тестовый PDF из test_rules
    from ai_checks.tests.test_rules import PAGE_W, PAGE_H
    from ai_checks.tests.pdfgen import make_pdf_bytes
    from ai_checks.tests.base import find_cyrillic_font
    
    # Создаем PDF со штампом
    data = make_pdf_bytes(
        rects=[(400, 780, 575, 825)],  # штамп в правом нижнем углу
        texts=[('Основная надпись', 8, (400, 720))],
    )
    tmp_path = '/tmp/test_stamp.pdf'
    with open(tmp_path, 'wb') as f:
        f.write(data)
    pdf_files = [Path(tmp_path)]

for pdf_path in pdf_files:
    print(f"\n{'='*60}")
    print(f"PDF: {pdf_path}")
    print(f"{'='*60}")
    
    with pdfium_tools.open_pdf(str(pdf_path)) as doc:
        view = pdfium_tools.PdfPageView(doc, 0)
        
        print(f"\n--- page.rect (displayed, с /Rotate) ---")
        print(f"  rect = {view.rect}")
        print(f"  width={view.rect.width:.1f}, height={view.rect.height:.1f}")
        
        print(f"\n--- page.mediabox (unrotated user space) ---")
        print(f"  mediabox = {view.mediabox}")
        print(f"  width={view.mediabox.width:.1f}, height={view.mediabox.height:.1f}")
        
        print(f"\n--- cropbox ---")
        print(f"  cropbox = {view._cropbox}")
        
        print(f"\n--- rotation ---")
        print(f"  rotation = {view.rotation}")
        
        # Зона штампа как сейчас (из page.rect)
        rect = view.rect
        STAMP_ZONE_X = 0.6
        STAMP_ZONE_Y = 0.7
        zone_from_rect = pdfium_tools.Rect(
            rect.x0 + rect.width * STAMP_ZONE_X,
            rect.y0 + rect.height * STAMP_ZONE_Y,
            rect.x1,
            rect.y1,
        )
        print(f"\n--- Зона штампа (из page.rect — ТЕКУЩИЙ код) ---")
        print(f"  zone = {zone_from_rect}")
        
        # Зона штампа из mediabox (исправление)
        mb = view.mediabox
        zone_from_mb = pdfium_tools.Rect(
            mb.x0 + mb.width * STAMP_ZONE_X,
            mb.y0 + mb.height * STAMP_ZONE_Y,
            mb.x1,
            mb.y1,
        )
        print(f"\n--- Зона штампа (из mediabox — ИСПРАВЛЕНИЕ) ---")
        print(f"  zone = {zone_from_mb}")
        
        # Считаем сегменты
        segments = list(view.iter_line_segments())
        print(f"\n--- iter_line_segments() ---")
        print(f"  Всего сегментов: {len(segments)}")
        if segments:
            print(f"  Первые 5: {segments[:5]}")
        
        # Считаем прямоугольники
        rects = list(view.iter_rects())
        print(f"\n--- iter_rects() ---")
        print(f"  Всего прямоугольников: {len(rects)}")
        if rects:
            for i, r in enumerate(rects[:10]):
                print(f"  [{i}] {r}")
        
        # Проверяем пересечение с зоной
        print(f"\n--- Пересечение сегментов с зоной (из rect) ---")
        zone = zone_from_rect
        h_count = 0
        v_count = 0
        for x0, y0, x1, y1 in segments:
            seg_x0, seg_x1 = min(x0, x1), max(x0, x1)
            seg_y0, seg_y1 = min(y0, y1), max(y0, y1)
            hits = (seg_x1 >= zone.x0 and seg_x0 <= zone.x1 and
                    seg_y1 >= zone.y0 and seg_y0 <= zone.y1)
            if hits:
                if abs(y1 - y0) <= 1.5 and abs(x1 - x0) >= 20:
                    h_count += 1
                elif abs(x1 - x0) <= 1.5 and abs(y1 - y0) >= 20:
                    v_count += 1
        print(f"  Горизонтальных в зоне: {h_count}")
        print(f"  Вертикальных в зоне: {v_count}")
        
        print(f"\n--- Пересечение сегментов с зоной (из mediabox) ---")
        zone = zone_from_mb
        h_count2 = 0
        v_count2 = 0
        for x0, y0, x1, y1 in segments:
            seg_x0, seg_x1 = min(x0, x1), max(x0, x1)
            seg_y0, seg_y1 = min(y0, y1), max(y0, y1)
            hits = (seg_x1 >= zone.x0 and seg_x0 <= zone.x1 and
                    seg_y1 >= zone.y0 and seg_y0 <= zone.y1)
            if hits:
                if abs(y1 - y0) <= 1.5 and abs(x1 - x0) >= 20:
                    h_count2 += 1
                elif abs(x1 - x0) <= 1.5 and abs(y1 - y0) >= 20:
                    v_count2 += 1
        print(f"  Горизонтальных в зоне: {h_count2}")
        print(f"  Вертикальных в зоне: {v_count2}")
        
        # Проверяем прямоугольники с зоной
        print(f"\n--- Прямоугольники, примыкающие к правому нижнему углу ---")
        edge_tol = rect.width * 0.05
        for r in rects:
            if r.x1 >= rect.x1 - edge_tol and r.y1 >= rect.y1 - edge_tol:
                print(f"  НАЙДЕН: {r} (x1={r.x1:.1f} >= {rect.x1 - edge_tol:.1f})")
            else:
                print(f"  пропущен: {r} (x1={r.x1:.1f} < {rect.x1 - edge_tol:.1f})")
        
        # Текст в зоне
        print(f"\n--- Текст в зоне (из rect) ---")
        text1 = view.get_text(clip=zone_from_rect)
        print(f"  Текст: {repr(text1[:200])}")
        
        print(f"\n--- Текст в зоне (из mediabox) ---")
        text2 = view.get_text(clip=zone_from_mb)
        print(f"  Текст: {repr(text2[:200])}")
        
        view.close()
        doc.close()
        break
