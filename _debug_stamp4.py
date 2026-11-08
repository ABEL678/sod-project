"""Проверка реальных PDF из media на наличие штампа."""

import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sod_core.settings')

import django
django.setup()

from ai_checks import pdfium_tools
from ai_checks.domain.rules import check_stamp, STAMP_ZONE_X, STAMP_ZONE_Y
from pathlib import Path

media_root = Path('media')
pdf_files = list(media_root.rglob('*.pdf'))[:10]

print(f"Найдено PDF: {len(pdf_files)}\n")

for pdf_path in pdf_files:
    try:
        with pdfium_tools.open_pdf(str(pdf_path)) as doc:
            for idx in range(len(doc)):
                view = pdfium_tools.PdfPageView(doc, idx)
                
                findings = check_stamp(view)
                
                if findings:
                    print(f"{'='*60}")
                    print(f"PDF: {pdf_path.relative_to('media')}")
                    print(f"  Страница: {idx+1}")
                    print(f"  rotation={view.rotation}")
                    print(f"  rect={view.rect}")
                    print(f"  mediabox={view.mediabox}")
                    print(f"  НАХОДКА: {findings[0]['message']}")
                    print(f"  evidence: {findings[0]['evidence']}")
                view.close()
    except Exception as e:
        print(f"Ошибка {pdf_path}: {e}")
