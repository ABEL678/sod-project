"""АВТОГЕНЕРАТ из static/design-tokens/tokens.json — не редактировать вручную.

Правки вносить в tokens.json, затем: python scripts/build_design_tokens.py
"""

TOKENS_VERSION = '20261102'
DEFAULT_STATUS = 'NEW'

# HEX-цвета статусов замечаний (совпадают с tokens.css и design-tokens.js).
ISSUE_STATUS_HEX = {
    'CANCELLED': '#212529',
    'CLOSED': '#6c757d',
    'DISCUSSION': '#ffc107',
    'DONE': '#198754',
    'IN_PROGRESS': '#0dcaf0',
    'NEW': '#0d6efd',
    'REJECTED': '#dc3545',
}

# RGB 0..1 для PyMuPDF (экспорт PDF с маркапами): services.PDF_ISSUE_COLORS.
ISSUE_STATUS_RGB = {
    'CANCELLED': (0.13, 0.15, 0.16),  # #212529
    'CLOSED': (0.42, 0.46, 0.49),  # #6c757d
    'DISCUSSION': (1.0, 0.76, 0.03),  # #ffc107
    'DONE': (0.1, 0.53, 0.33),  # #198754
    'IN_PROGRESS': (0.05, 0.79, 0.94),  # #0dcaf0
    'NEW': (0.05, 0.43, 0.99),  # #0d6efd
    'REJECTED': (0.86, 0.21, 0.27),  # #dc3545
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
EXCEL_HEADER_FILL = '#EFEFEC'
EXCEL_OVERDUE_TEXT = '#B02A37'
EXCEL_LINK = '#0563C1'
