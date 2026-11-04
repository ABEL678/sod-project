"""АВТОГЕНЕРАТ из static/design-tokens/tokens.json — не редактировать вручную.

Правки вносить в tokens.json, затем: python scripts/build_design_tokens.py
"""

TOKENS_VERSION = '20261108.1'
DEFAULT_STATUS = 'NEW'

# HEX-цвета статусов замечаний (совпадают с tokens.css и design-tokens.js).
ISSUE_STATUS_HEX = {
    'CANCELLED': '#3F3F3C',
    'CLOSED': '#5D6770',
    'DISCUSSION': '#9A650F',
    'DONE': '#3F7D56',
    'IN_PROGRESS': '#2E7D8C',
    'NEW': '#4674A4',
    'REJECTED': '#B44E48',
}

# RGB 0..1 для PyMuPDF (экспорт PDF с маркапами): services.PDF_ISSUE_COLORS.
ISSUE_STATUS_RGB = {
    'CANCELLED': (0.25, 0.25, 0.24),  # #3F3F3C
    'CLOSED': (0.36, 0.4, 0.44),  # #5D6770
    'DISCUSSION': (0.6, 0.4, 0.06),  # #9A650F
    'DONE': (0.25, 0.49, 0.34),  # #3F7D56
    'IN_PROGRESS': (0.18, 0.49, 0.55),  # #2E7D8C
    'NEW': (0.27, 0.45, 0.64),  # #4674A4
    'REJECTED': (0.71, 0.31, 0.28),  # #B44E48
}

# CSS-классы бейджей для шаблонов и экспорта.
ISSUE_STATUS_CLASS = {
    'CANCELLED': 'cde-badge cde-badge--canceled',
    'CLOSED': 'cde-badge cde-badge--closed',
    'DISCUSSION': 'cde-badge cde-badge--review',
    'DONE': 'cde-badge cde-badge--done',
    'IN_PROGRESS': 'cde-badge cde-badge--progress',
    'NEW': 'cde-badge cde-badge--wait',
    'REJECTED': 'cde-badge cde-badge--attention',
}

# Цвета Excel-экспорта (не входят в CSS/JS).
# Спринт 3: без «#» — openpyxl требует aRGB-строку, иначе export падает.
EXCEL_HEADER_FILL = 'EFEFEC'
EXCEL_OVERDUE_TEXT = 'B02A37'
EXCEL_LINK = '0563C1'
