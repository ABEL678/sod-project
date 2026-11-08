"""Диагностика SPDS.STAMP на тестовом PDF со штампом."""

import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sod_core.settings')

import django
django.setup()

from ai_checks import pdfium_tools
from ai_checks.tests.pdfgen import make_pdf_bytes

# Создаем PDF со штампом (как в test_rules)
PAGE_W, PAGE_H = 595, 842
STAMP_ZONE_X, STAMP_ZONE_Y = 0.6, 0.7

# Вариант 1: штамп по тексту + линиям (ГОСТ-штамп)
data = make_pdf_bytes(
    width=PAGE_W, height=PAGE_H,
    rects=[
        # Рамка
        (20, 20, PAGE_W - 20, PAGE_H - 20),
        # Рамка штампа (примыкает к правому нижнему углу)
        (400, 780, 575, 825),
    ],
    texts=[
        ('Основная надпись', 8, (400, 720)),
        ('Стадия', 8, (400, 680)),
        ('Лист', 8, (450, 680)),
        ('Листов', 8, (500, 680)),
    ],
)

tmp_path = '/tmp/test_stamp_gost.pdf'
with open(tmp_path, 'wb') as f:
    f.write(data)

print(f"{'='*60}")
print(f"PDF: {tmp_path} (ГОСТ-штамп с текстом и рамкой)")
print(f"{'='*60}")

with pdfium_tools.open_pdf(tmp_path) as doc:
    view = pdfium_tools.PdfPageView(doc, 0)
    
    print(f"\n--- page.rect ---")
    print(f"  rect = {view.rect}")
    
    print(f"\n--- page.mediabox ---")
    print(f"  mediabox = {view.mediabox}")
    
    # Зона штампа
    rect = view.rect
    zone = pdfium_tools.Rect(
        rect.x0 + rect.width * STAMP_ZONE_X,
        rect.y0 + rect.height * STAMP_ZONE_Y,
        rect.x1,
        rect.y1,
    )
    print(f"\n--- Зона штампа ---")
    print(f"  zone = {zone}")
    
    # Сегменты
    segments = list(view.iter_line_segments())
    print(f"\n--- iter_line_segments() ---")
    print(f"  Всего: {len(segments)}")
    
    # Ищем горизонтальные и вертикальные линии в зоне
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
                print(f"    горизонтальная в зоне: ({x0:.1f},{y0:.1f})->({x1:.1f},{y1:.1f})")
            elif abs(x1 - x0) <= 1.5 and abs(y1 - y0) >= 20:
                v_count += 1
                print(f"    вертикальная в зоне: ({x0:.1f},{y0:.1f})->({x1:.1f},{y1:.1f})")
    print(f"  Итого в зоне: {h_count} гор., {v_count} верт.")
    
    # Прямоугольники
    rects = list(view.iter_rects())
    print(f"\n--- iter_rects() ---")
    print(f"  Всего: {len(rects)}")
    for i, r in enumerate(rects):
        print(f"  [{i}] {r}")
    
    # Прямоугольник штампа (примыкает к правому нижнему углу)
    edge_tol = rect.width * 0.05
    print(f"\n--- Проверка прямоугольников на примыкание к правому нижнему углу ---")
    print(f"  edge_tol = {edge_tol:.1f}")
    for r in rects:
        matches = r.x1 >= rect.x1 - edge_tol and r.y1 >= rect.y1 - edge_tol
        print(f"  {r}: x1={r.x1:.1f}>={rect.x1 - edge_tol:.1f} AND y1={r.y1:.1f}>={rect.y1 - edge_tol:.1f} => {'ДА' if matches else 'НЕТ'}")
    
    # Текст
    print(f"\n--- Текст в зоне ---")
    text = view.get_text(clip=zone)
    print(f"  Текст: {repr(text[:300])}")
    
    # Проверка меток
    STAMP_LABELS = ('стадия', 'лист', 'листов', 'масштаб', 'разраб', 'пров',
                    'н.контр', 'нормоконтр', 'гип', 'утв', 'шифр', 'изм.')
    normalized = ' '.join(text.replace('\xa0', ' ').split()).casefold()
    matched = [l for l in STAMP_LABELS if l in normalized]
    print(f"\n--- Метки штампа ---")
    print(f"  Найдено: {matched}")
    
    view.close()
    doc.close()
