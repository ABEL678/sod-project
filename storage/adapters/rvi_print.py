"""Печатные формы РВИ (9/9а) и таблицы регистрации изменений (форма 10).

HTML-версии форм живут в шаблонах ``change_permission_print.html`` и
``document_change_table.html``; этот модуль даёт общую разбивку на
страницы и PDF-выгрузку через reportlab — библиотеку, уже принятую в
проекте для печатных форм (см. PDF «Матрицы заданий»).

Размеры граф: A4 альбомная, поля 10 мм, рабочая ширина 277 мм.
Графы 1–8 (28/45/52/11/14/88/10/29 мм) сняты по пропорциям форм 9/9а
приложения К; страница 1 — форма 9, последующие — форма 9а.
"""

from io import BytesIO


# Ширина граф 1–8, мм. Сумма — 277 мм (297 − 2×10).
RVI_COLUMN_WIDTHS_MM = (28, 45, 52, 11, 14, 88, 10, 29)

RVI_FIRST_PAGE_CAPACITY_MM = 104
RVI_NEXT_PAGE_CAPACITY_MM = 142


def _content_lines(text: str) -> int:
    """Оценка числа строк содержания при ширине графы 6 (88 мм)."""
    lines = 0
    for part in (text or '').splitlines() or ['']:
        lines += max(1, (len(part) + 84) // 85)
    return lines


def _row_height_mm(row: dict) -> float:
    """Оценка высоты строки: шапка документа + содержание + отбивка."""
    return 8.0 + _content_lines(row.get('content')) * 4.6 + 4.0


def build_change_permission_pages(data: dict) -> list[list[dict]]:
    """Разбивает строки формы 9/9а на страницы.

    Первая страница — форма 9, каждая следующая — форма 9а. Нехватка
    места определяется по оценке высоты строк; хотя бы одна строка
    попадает на страницу всегда.
    """
    rows = data.get('rows') or []
    pages: list[list[dict]] = []
    current: list[dict] = []
    used = 0.0
    for row in rows:
        capacity = (
            RVI_FIRST_PAGE_CAPACITY_MM
            if not pages
            else RVI_NEXT_PAGE_CAPACITY_MM
        )
        height = _row_height_mm(row)
        if current and used + height > capacity:
            pages.append(current)
            current = []
            used = 0.0
        current.append(row)
        used += height
    if current:
        pages.append(current)
    if not pages:
        pages = [[{
            'document_code': '—',
            'change_number': '—',
            'sheets': '—',
            'content': '',
            'reason_code': '—',
            'note': '—',
        }]]
    return pages


def _pdf_fonts():
    """TTF-шрифт с кириллицей (тот же порядок поиска, что у матрицы)."""
    import sys
    from pathlib import Path

    from django.conf import settings
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    roots = []
    base_dir = getattr(settings, 'BASE_DIR', None)
    if base_dir is not None:
        roots.append(Path(base_dir))
    roots.append(Path(__file__).resolve().parents[2])

    regular = []
    bold = []
    for root in roots:
        fonts_dir = root / 'static' / 'fonts'
        regular.extend([
            ('GolosText', fonts_dir / 'GolosText-Regular.ttf'),
            ('GolosText', fonts_dir / 'GolosText.ttf'),
            ('DejaVuSans', fonts_dir / 'DejaVuSans.ttf'),
        ])
        bold.extend([
            ('GolosText-Bold', fonts_dir / 'GolosText-Bold.ttf'),
            ('DejaVuSans-Bold', fonts_dir / 'DejaVuSans-Bold.ttf'),
        ])
    if sys.platform != 'win32':
        system = Path('/usr/share/fonts/truetype/dejavu')
        regular.append(('DejaVuSans', system / 'DejaVuSans.ttf'))
        bold.append(('DejaVuSans-Bold', system / 'DejaVuSans-Bold.ttf'))

    for name, path in regular:
        if not path.exists():
            continue
        pdfmetrics.registerFont(TTFont(name, str(path)))
        bold_name = name
        for candidate, candidate_path in bold:
            if candidate_path.exists():
                pdfmetrics.registerFont(
                    TTFont(candidate, str(candidate_path)),
                )
                bold_name = candidate
                break
        addMapping(name, 0, 0, name)
        addMapping(name, 1, 0, bold_name)
        return name, bold_name

    raise RuntimeError(
        'Экспорт PDF РВИ недоступен: не найден TTF-шрифт с кириллицей. '
        'Положите GolosText-Regular.ttf или DejaVuSans.ttf в static/fonts/.'
    )


def _date_short(value) -> str:
    """Дата подписи — две последние цифры года (графы подписей)."""
    return value.strftime('%d.%m.%y') if value else '—'


def _person(user) -> str:
    if user is None:
        return '—'
    return user.get_full_name() or user.username


def _escape(text: str) -> str:
    from xml.sax.saxutils import escape

    return escape(str(text or '')).replace('\n', '<br/>')


def build_change_permission_pdf(data: dict) -> BytesIO:
    """PDF формы 9 (первый лист) и 9а (последующие), альбомная A4."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    font, font_bold = _pdf_fonts()
    styles = {
        'title': ParagraphStyle(
            'rvi-title', fontName=font_bold, fontSize=11, leading=13,
            alignment=TA_CENTER,
        ),
        'form': ParagraphStyle(
            'rvi-form', fontName=font_bold, fontSize=8, leading=10,
            alignment=TA_RIGHT,
        ),
        'head': ParagraphStyle(
            'rvi-head', fontName=font_bold, fontSize=6.5, leading=7.5,
            alignment=TA_CENTER,
        ),
        'cell': ParagraphStyle(
            'rvi-cell', fontName=font, fontSize=7, leading=8.4,
        ),
        'content': ParagraphStyle(
            'rvi-content', fontName=font, fontSize=7, leading=8.6,
        ),
        'sign': ParagraphStyle(
            'rvi-sign', fontName=font, fontSize=6.3, leading=7.4,
            alignment=TA_CENTER,
        ),
        'caption': ParagraphStyle(
            'rvi-caption', fontName=font, fontSize=6.5, leading=8,
            alignment=TA_RIGHT,
        ),
    }

    col_widths = [width * mm for width in RVI_COLUMN_WIDTHS_MM]
    pages = build_change_permission_pages(data)
    permission = data['permission']

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=8 * mm,
        bottomMargin=8 * mm,
        title=f'Разрешение на внесение изменений {data["code"]}',
        author='СОД',
    )
    story = []
    for page_index, rows in enumerate(pages):
        form_no = '9' if page_index == 0 else '9а'
        story.append(Paragraph(
            'Разрешение на внесение изменений', styles['title'],
        ))
        story.append(Paragraph(f'Форма {form_no}', styles['form']))
        story.append(Spacer(1, 2 * mm))

        table_data = [
            [str(number) for number in range(1, 9)],
            [
                Paragraph('Разрешение', styles['head']),
                Paragraph('Обозначение', styles['head']),
                Paragraph(
                    'Наименование объекта строительства', styles['head'],
                ),
                Paragraph('Изм.', styles['head']),
                Paragraph('Лист', styles['head']),
                Paragraph('Содержание изменения', styles['head']),
                Paragraph('Код', styles['head']),
                Paragraph('Примечание', styles['head']),
            ],
        ]
        for row_index, row in enumerate(rows):
            content = (
                f'<u>{_escape(row["document_code"])}</u><br/>'
                f'{_escape(row["content"])}'
            )
            table_data.append([
                Paragraph(_escape(data['code']), styles['cell'])
                if row_index == 0 else '',
                Paragraph(_escape(data['designations']), styles['cell'])
                if row_index == 0 else '',
                Paragraph(_escape(data['project_name']), styles['cell'])
                if row_index == 0 else '',
                Paragraph(str(row['change_number']), styles['cell']),
                Paragraph(_escape(row['sheets']), styles['cell']),
                Paragraph(content, styles['content']),
                Paragraph(_escape(row['reason_code']), styles['cell']),
                Paragraph(_escape(row['note']), styles['cell']),
            ])
        table = Table(table_data, colWidths=col_widths, repeatRows=2)
        last_row = len(table_data) - 1
        style_commands = [
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('FONTNAME', (0, 0), (-1, -1), font),
            ('FONTSIZE', (0, 0), (-1, -1), 6.5),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 1.2 * mm),
            ('RIGHTPADDING', (0, 0), (-1, -1), 1.2 * mm),
            ('TOPPADDING', (0, 0), (-1, -1), 0.8 * mm),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0.8 * mm),
        ]
        if len(rows) > 1:
            style_commands.extend([
                ('SPAN', (0, 2), (0, last_row)),
                ('SPAN', (1, 2), (1, last_row)),
                ('SPAN', (2, 2), (2, last_row)),
            ])
        table.setStyle(TableStyle(style_commands))
        story.append(table)
        story.append(Spacer(1, 2 * mm))

        author = _person(data['author'])
        gip = _person(data['gip'])
        approved = _person(data['approved_by'])
        sign_data = [
            [
                Paragraph('Изм. внес', styles['head']),
                Paragraph('Лист', styles['head']),
                Paragraph('Листов', styles['head']),
                Paragraph('Составил', styles['head']),
                Paragraph('ГИП', styles['head']),
                Paragraph('Утв.', styles['head']),
            ],
            [
                Paragraph(_escape(author), styles['sign']),
                Paragraph('—', styles['sign']),
                Paragraph('—', styles['sign']),
                Paragraph(
                    f'{_escape(author)}<br/>{_date_short(data["created_at"])}',
                    styles['sign'],
                ),
                Paragraph(
                    f'{_escape(gip)}<br/>{_date_short(data["approved_at"])}',
                    styles['sign'],
                ),
                Paragraph(
                    f'{_escape(approved)}<br/>'
                    f'{_date_short(data["approved_at"])}',
                    styles['sign'],
                ),
            ],
        ]
        sign_widths = [46 * mm, 26 * mm, 30 * mm, 59 * mm, 58 * mm, 58 * mm]
        sign_table = Table(sign_data, colWidths=sign_widths)
        sign_table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 1.2 * mm),
            ('RIGHTPADDING', (0, 0), (-1, -1), 1.2 * mm),
            ('TOPPADDING', (0, 0), (-1, -1), 0.8 * mm),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0.8 * mm),
        ]))
        story.append(sign_table)
        story.append(Spacer(1, 1.5 * mm))

        approvals = Table(
            [
                ['13', '14', '15', '16'],
                ['—', '—', '—', '—'],
            ],
            colWidths=[69.25 * mm] * 4,
        )
        approvals.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, 0), (-1, -1), font),
            ('FONTSIZE', (0, 0), (-1, -1), 6.5),
        ]))
        story.append(approvals)
        story.append(Spacer(1, 1 * mm))
        story.append(Paragraph('Согласования / нормоконтроль', styles['caption']))
        story.append(Paragraph(
            f'{permission.project.code} · {data["code"]} · Форма {form_no}',
            styles['caption'],
        ))

        if page_index < len(pages) - 1:
            story.append(PageBreak())

    doc.build(story)
    buffer.seek(0)
    return buffer


def build_document_change_table_pdf(document, rows) -> BytesIO:
    """PDF формы 10 (таблица регистрации изменений, приложение М)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    font, font_bold = _pdf_fonts()
    styles = {
        'title': ParagraphStyle(
            'ct-title', fontName=font_bold, fontSize=11, leading=13,
        ),
        'subtitle': ParagraphStyle(
            'ct-subtitle', fontName=font, fontSize=8, leading=10,
        ),
        'head': ParagraphStyle(
            'ct-head', fontName=font_bold, fontSize=6.5, leading=7.5,
        ),
        'cell': ParagraphStyle(
            'ct-cell', fontName=font, fontSize=7, leading=8.4,
        ),
    }
    headers = [
        'Изм.',
        'Номер док.',
        'Дата',
        'Листы изменённые',
        'Листы заменённые',
        'Листы новые',
        'Листы аннулированные',
        'Всего листов в док.',
    ]
    table_data = [[Paragraph(header, styles['head']) for header in headers]]
    for row in rows:
        table_data.append([
            Paragraph(str(row['change_number']), styles['cell']),
            Paragraph(_escape(row['permission_code']), styles['cell']),
            Paragraph(_date_short(row['date']), styles['cell']),
            Paragraph(row['changed_sheets'], styles['cell']),
            Paragraph(row['replaced_sheets'], styles['cell']),
            Paragraph(row['new_sheets'], styles['cell']),
            Paragraph(row['annulled_sheets'], styles['cell']),
            Paragraph(row['total_sheets'], styles['cell']),
        ])
    if not rows:
        table_data.append([
            Paragraph('—', styles['cell']) for _ in headers
        ])

    widths = [
        16 * mm, 35 * mm, 24 * mm, 36 * mm, 36 * mm, 30 * mm, 45 * mm, 35 * mm,
    ]
    table = Table(table_data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 1.2 * mm),
        ('RIGHTPADDING', (0, 0), (-1, -1), 1.2 * mm),
        ('TOPPADDING', (0, 0), (-1, -1), 0.8 * mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0.8 * mm),
    ]))

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=f'Таблица регистрации изменений {document.code}',
        author='СОД',
    )
    doc.build([
        Paragraph('Таблица регистрации изменений', styles['title']),
        Paragraph(
            f'{document.project.code} · {document.code} — {document.name}',
            styles['subtitle'],
        ),
        Spacer(1, 3 * mm),
        table,
    ])
    buffer.seek(0)
    return buffer