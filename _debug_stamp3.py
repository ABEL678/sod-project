"""Диагностика SPDS.STAMP на повёрнутой странице."""

import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sod_core.settings')

import django
django.setup()

from ai_checks import pdfium_tools
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as rl_canvas
from io import BytesIO

# Создаем PDF с поворотом через /Rotate
buf = BytesIO()
c = rl_canvas.Canvas(buf, pagesize=A4)
c.drawString(100, 700, 'Тест поворота')
c.drawString(400, 720, 'Основная надпись')
# Рамка штампа в правом нижнем углу (TL-координаты PyMuPDF)
c.rect(400, 842 - 825, 175, 45)  # x=400, y=842-825=17, w=175, h=45
c.showPage()
c.save()
data = buf.getvalue()

# Добавляем /Rotate 90 через байтовую подмену
# PDF создается с portrait (595x842), добавим /Rotate=90
# Найдем страницу и добавим rotate
import re
# Вставляем /Rotate 90 в страницу
page_obj = re.search(rb'(q.*?Q)', data, re.DOTALL)
# Проще: вставим /Rotate в page dictionary
# Найдем stream и добавим /Rotate перед ним
rotated = data.replace(b'/Length ', b'/Rotate 90\n/Length ')

tmp_path = '/tmp/test_stamp_rotated.pdf'
with open(tmp_path, 'wb') as f:
    f.write(rotated)

print(f"{'='*60}")
print(f"PDF: {tmp_path} (A4 portrait + /Rotate 90)")
print(f"{'='*60}")

with pdfium_tools.open_pdf(tmp_path) as doc:
    view = pdfium_tools.PdfPageView(doc, 0)
    
    print(f"\n--- page.rect (displayed, с /Rotate) ---")
    print(f"  rect = {view.rect}")
    print(f"  width={view.rect.width:.1f}, height={view.rect.height:.1f}")
    
    print(f"\n--- page.mediabox (unrotated) ---")
    print(f"  mediabox = {view.mediabox}")
    print(f"  width={view.mediabox.width:.1f}, height={view.mediabox.height:.1f}")
    
    print(f"\n--- rotation ---")
    print(f"  rotation = {view.rotation}")
    
    # Зона штампа из page.rect (ТЕКУЩИЙ код)
    rect = view.rect
    STAMP_ZONE_X, STAMP_ZONE_Y = 0.6, 0.7
    zone_from_rect = pdfium_tools.Rect(
        rect.x0 + rect.width * STAMP_ZONE_X,
        rect.y0 + rect.height * STAMP_ZONE_Y,
        rect.x1,
        rect.y1,
    )
    print(f"\n--- Зона штампа (из page.rect — ТЕКУЩИЙ) ---")
    print(f"  zone = {zone_from_rect}")
    
    # Зона штампа из mediabox (ИСПРАВЛЕНИЕ)
    mb = view.mediabox
    zone_from_mb = pdfium_tools.Rect(
        mb.x0 + mb.width * STAMP_ZONE_X,
        mb.y0 + mb.height * STAMP_ZONE_Y,
        mb.x1,
        mb.y1,
    )
    print(f"\n--- Зона штампа (из mediabox — ИСПРАВЛЕНИЕ) ---")
    print(f"  zone = {zone_from_mb}")
    
    # Сегменты
    segments = list(view.iter_line_segments())
    print(f"\n--- iter_line_segments() ---")
    print(f"  Всего: {len(segments)}")
    if segments:
        print(f"  Первые 5:")
        for s in segments[:5]:
            print(f"    {s}")
    
    # Проверка пересечения с зоной из rect
    print(f"\n--- Пересечение сегментов с zone(rect) ---")
    zone = zone_from_rect
    h = v = 0
    for x0, y0, x1, y1 in segments:
        hits = (min(x1,x1) >= zone.x0 and max(x0,x1) <= zone.x1 and
                min(y0,y1) >= zone.y0 and max(y0,y1) <= zone.y1)
        if hits:
            if abs(y1-y0) <= 1.5 and abs(x1-x0) >= 20: h += 1
            elif abs(x1-x0) <= 1.5 and abs(y1-y0) >= 20: v += 1
    print(f"  В зоне: {h} гор., {v} верт.")
    
    # Проверка пересечения с зоной из mediabox
    print(f"\n--- Пересечение сегментов с zone(mediabox) ---")
    zone = zone_from_mb
    h2 = v2 = 0
    for x0, y0, x1, y1 in segments:
        hits = (min(x1,x1) >= zone.x0 and max(x0,x1) <= zone.x1 and
                min(y0,y1) >= zone.y0 and max(y0,y1) <= zone.y1)
        if hits:
            if abs(y1-y0) <= 1.5 and abs(x1-x0) >= 20: h2 += 1
            elif abs(x1-x0) <= 1.5 and abs(y1-y0) >= 20: v2 += 1
    print(f"  В зоне: {h2} гор., {v2} верт.")
    
    # Прямоугольники
    rects = list(view.iter_rects())
    print(f"\n--- iter_rects() ---")
    print(f"  Всего: {len(rects)}")
    for i, r in enumerate(rects):
        print(f"  [{i}] {r}")
    
    # Проверка примыкания к правому нижнему углу
    print(f"\n--- Примыкание к правому нижнему углу (rect.x1={rect.x1:.1f}) ---")
    edge_tol = rect.width * 0.05
    for r in rects:
        matches = r.x1 >= rect.x1 - edge_tol and r.y1 >= rect.y1 - edge_tol
        print(f"  {r}: {'ДА' if matches else 'НЕТ'}")
    
    # Текст в зоне
    print(f"\n--- Текст в zone(rect) ---")
    text1 = view.get_text(clip=zone_from_rect)
    print(f"  {repr(text1[:200])}")
    
    print(f"\n--- Текст в zone(mediabox) ---")
    text2 = view.get_text(clip=zone_from_mb)
    print(f"  {repr(text2[:200])}")
    
    view.close()
    doc.close()
