"""Детерминированные правила проверки листов (этап Ф1, без LLM).

Каждое правило — функция от страницы PyMuPDF, возвращающая список
находок-словарей: {page, bbox, message, severity, evidence}.
bbox — координаты PyMuPDF в пунктах [x0, y0, x1, y1] по ARCHITECTURE 3.2.

Движок (services.run_rules_check) сопоставляет RuleDefinition.code →
функция из RULE_CHECKS. Structural-правила (рамка/штамп/шрифт) — код;
rule_kind в БД хранит спековую классификацию (regex/keyword/manual).
"""

from __future__ import annotations

from typing import Callable

# 2.5 мм в пунктах (1 pt = 25.4 / 72 мм).
MIN_FONT_MM = 2.5
MIN_FONT_PT = MIN_FONT_MM * 72.0 / 25.4  # ≈ 7.087 pt

# Доли страницы для зоны основной надписи (правый нижний угол).
STAMP_ZONE_X = 0.6
STAMP_ZONE_Y = 0.7

# Допуск для линий рамки и границы краёв (доли страницы).
FRAME_EDGE_TOLERANCE = 0.08
LINE_TOLERANCE_PT = 1.5


def _page_finding(page, bbox, message, severity, evidence='') -> dict:
    return {
        'page': page.number + 1,
        'bbox': (
            [round(float(value), 2) for value in bbox]
            if bbox is not None else None
        ),
        'message': message,
        'severity': severity,
        'evidence': evidence,
    }


def _iter_segments(page):
    """Линейные сегменты (горизонтальные/вертикальные) из drawings."""
    for drawing in page.get_drawings():
        for item in drawing['items']:
            if item[0] == 'l':
                point_a, point_b = item[1], item[2]
                yield (point_a.x, point_a.y, point_b.x, point_b.y)
            elif item[0] == 're':
                rect = item[1]
                yield (rect.x0, rect.y0, rect.x1, rect.y0)
                yield (rect.x0, rect.y1, rect.x1, rect.y1)
                yield (rect.x0, rect.y0, rect.x0, rect.y1)
                yield (rect.x1, rect.y0, rect.x1, rect.y1)


def check_frame(page) -> list[dict]:
    """SPDS.RAME: наличие внешней рамки листа.

    Рамка считается найденной, если есть длинная горизонтальная линия
    у верхнего И нижнего края и длинная вертикальная — у левого И правого.
    """
    rect = page.rect
    width = max(rect.width, 1.0)
    height = max(rect.height, 1.0)
    edge_x = width * FRAME_EDGE_TOLERANCE
    edge_y = height * FRAME_EDGE_TOLERANCE
    min_horizontal = width * 0.8
    min_vertical = height * 0.8

    top = bottom = left = right = False
    for x0, y0, x1, y1 in _iter_segments(page):
        if abs(y1 - y0) <= LINE_TOLERANCE_PT and abs(x1 - x0) >= min_horizontal:
            edge = (y0 + y1) / 2.0
            if edge <= rect.y0 + edge_y:
                top = True
            if edge >= rect.y1 - edge_y:
                bottom = True
        if abs(x1 - x0) <= LINE_TOLERANCE_PT and abs(y1 - y0) >= min_vertical:
            edge = (x0 + x1) / 2.0
            if edge <= rect.x0 + edge_x:
                left = True
            if edge >= rect.x1 - edge_x:
                right = True

    if top and bottom and left and right:
        return []
    missing = [
        name for name, found in (
            ('верх', top), ('низ', bottom),
            ('лево', left), ('право', right),
        ) if not found
    ]
    # bbox = None: рамка — свойство всего листа, выделять прямоугольником
    # всю страницу не нужно (находка уровня страницы; пин встанет в центр).
    return [_page_finding(
        page,
        None,
        'Не найдена рамка листа (СПДС)',
        'major',
        evidence='Отсутствуют линии: ' + ', '.join(missing),
    )]


# Характерные надписи ГОСТ-штампа (документация реальных чертежей:
# текст «Основная надпись» в штампе не встречается — это его название).
STAMP_LABELS = (
    'стадия',
    'лист',
    'листов',
    'масштаб',
    'разраб',
    'пров',
    'н.контр',
    'нормоконтр',
    'гип',
    'утв',
    'шифр',
    'изм.',
)

# Минимумы структуры таблицы штампа (тонкие линии, ГОСТ 21.101).
STAMP_MIN_H_LINES = 6
STAMP_MIN_V_LINES = 4


def _segment_hits_zone(x0, y0, x1, y1, zone) -> bool:
    """Пересечение сегмента с зоной (интервалы; сегменты нулевой толщины)."""
    seg_x0, seg_x1 = min(x0, x1), max(x0, x1)
    seg_y0, seg_y1 = min(y0, y1), max(y0, y1)
    return (
        seg_x1 >= zone.x0 and seg_x0 <= zone.x1
        and seg_y1 >= zone.y0 and seg_y0 <= zone.y1
    )


def check_stamp(page) -> list[dict]:
    """SPDS.STAMP: наличие основной надписи (штамп СПДС).

    Штамп реального чертежа — таблица тонких линий в правом нижнем углу
    (часто нарисованных прямоугольниками-штрихами), с полями «Стадия»,
    «Лист», «Листов», «Масштаб» и т.п. Признаки (любой):
    1) исторический текст «Основная надпись»;
    2) ≥2 характерные надписи ГОСТ-штампа в зоне;
    3) структура таблицы: ≥6 горизонтальных и ≥4 вертикальных тонких
       линий в зоне;
    4) рамка штампа, примыкающая к правому нижнему углу листа.
    """
    rect = page.rect
    zone = pymupdf_rect(
        rect.x0 + rect.width * STAMP_ZONE_X,
        rect.y0 + rect.height * STAMP_ZONE_Y,
        rect.x1,
        rect.y1,
    )

    text = page.get_text('text', clip=zone) or ''
    # PyMuPDF может отдавать неразрывные пробелы (\xa0) — нормализуем.
    normalized = ' '.join(text.replace('\xa0', ' ').split()).casefold()
    if 'основная надпись' in normalized:
        return []

    matched_labels = [label for label in STAMP_LABELS if label in normalized]
    if len(matched_labels) >= 2:
        return []

    horizontal = vertical = 0
    attached_box = False
    min_box_width = rect.width * 0.25
    min_box_height = rect.height * 0.03
    max_box_height = rect.height * 0.35
    edge_tolerance = rect.width * 0.05

    for drawing in page.get_drawings():
        for item in drawing['items']:
            segments = []
            if item[0] == 're':
                box = item[1]
                segments = [
                    (box.x0, box.y0, box.x1, box.y0),
                    (box.x0, box.y1, box.x1, box.y1),
                    (box.x0, box.y0, box.x0, box.y1),
                    (box.x1, box.y0, box.x1, box.y1),
                ]
                if (
                    box.width >= min_box_width
                    and min_box_height <= box.height <= max_box_height
                    and box.x1 >= rect.x1 - edge_tolerance
                    and box.y1 >= rect.y1 - edge_tolerance
                ):
                    attached_box = True
            elif item[0] == 'l':
                point_a, point_b = item[1], item[2]
                segments = [(point_a.x, point_a.y, point_b.x, point_b.y)]
            for x0, y0, x1, y1 in segments:
                if not _segment_hits_zone(x0, y0, x1, y1, zone):
                    continue
                if abs(y1 - y0) <= LINE_TOLERANCE_PT and abs(x1 - x0) >= 20:
                    horizontal += 1
                elif abs(x1 - x0) <= LINE_TOLERANCE_PT and abs(y1 - y0) >= 20:
                    vertical += 1

    if horizontal >= STAMP_MIN_H_LINES and vertical >= STAMP_MIN_V_LINES:
        return []
    if attached_box:
        return []

    return [_page_finding(
        page,
        [zone.x0, zone.y0, zone.x1, zone.y1],
        'Не найдена основная надпись (штамп)',
        'major',
        evidence=(
            'В правом нижнем углу нет штампа СПДС '
            f'(надписей: {len(matched_labels)}, '
            f'линий: {horizontal}/{vertical})'
        ),
    )]


def check_font(page) -> list[dict]:
    """SPDS.FONT: минимальная высота шрифта основного текста ≥ 2.5 мм.

    Штамп (правый нижний угол) исключается — его надписи регламентируются
    отдельно; проверяются прозаические текстовые спаны.
    """
    rect = page.rect
    stamp_x0 = rect.x0 + rect.width * STAMP_ZONE_X
    stamp_y0 = rect.y0 + rect.height * STAMP_ZONE_Y

    findings: list[dict] = []
    data = page.get_text('dict') or {}
    for block in data.get('blocks', []):
        if block.get('type', 0) != 0:
            continue
        for line in block.get('lines', []):
            for span in line.get('spans', []):
                text = (span.get('text') or '').strip()
                if not text:
                    continue
                x0, y0, x1, y1 = span['bbox']
                if x0 >= stamp_x0 and y0 >= stamp_y0:
                    continue
                size = float(span.get('size') or 0.0)
                if size and size < MIN_FONT_PT - 0.01:
                    millimeters = size * 25.4 / 72.0
                    findings.append(_page_finding(
                        page,
                        [x0, y0, x1, y1],
                        f'Высота шрифта {millimeters:.1f} мм меньше '
                        f'{MIN_FONT_MM} мм (СПДС)',
                        'minor',
                        evidence=text[:120],
                    ))
    return findings


def pymupdf_rect(x0, y0, x1, y1):
    """Локальная обёртка над pymupdf.Rect (импорт по требованию)."""
    import pymupdf

    return pymupdf.Rect(x0, y0, x1, y1)


# Реестр правил: RuleDefinition.code → проверка страницы.
RULE_CHECKS: dict[str, Callable] = {
    'SPDS.RAME': check_frame,
    'SPDS.STAMP': check_stamp,
    'SPDS.FONT': check_font,
}
