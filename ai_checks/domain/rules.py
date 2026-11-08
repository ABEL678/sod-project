"""Детерминированные правила проверки листов (этап Ф1, без LLM).

Каждое правило — функция от обёртки страницы pypdfium2
(ai_checks.pdfium_tools.PdfPageView), возвращающая список
находок-словарей: {page, bbox, message, severity, evidence}.
bbox — координаты в пунктах [x0, y0, x1, y1] по ARCHITECTURE 3.2
(неповёрнутая система, TL-начало — как в PyMuPDF, ради вьювера).

Движок (services.run_rules_check) сопоставляет RuleDefinition.code →
функция из RULE_CHECKS. Structural-правила (рамка/штамп/шрифт) — код;
rule_kind в БД хранит спековую классификацию (regex/keyword/manual).
"""

from __future__ import annotations

from typing import Callable

from ai_checks.pdfium_tools import Rect

# 2.5 мм в пунктах (1 pt = 25.4 / 72 мм).
MIN_FONT_MM = 2.5
MIN_FONT_PT = MIN_FONT_MM * 72.0 / 25.4  # ≈ 7.087 pt

# Доли страницы для зоны основной надписи (правый нижний угол).
STAMP_ZONE_X = 0.6
STAMP_ZONE_Y = 0.7

# Доля площади зоны штампа, которую должно закрыть растровое
# изображение, чтобы правило сочло штамп «растровым» (блок 1: скан
# листа/штампа не даёт ни векторной геометрии, ни текста).
STAMP_RASTER_COVERAGE = 0.8

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


def check_frame(page) -> list[dict]:
    """SPDS.RAME: наличие внешней рамки листа.

    Рамка считается найденной, если есть длинная горизонтальная линия
    у верхнего И нижнего края и длинная вертикальная — у левого И правого.

    Бэта-фикс 2026-11-08, блок 1: зона считается по mu_rect (MediaBox в
    MU-координатах геометрии), а не по page.rect — при /Rotate 90/270
    отображаемые размеры подменяют стороны, и рамка «терялась».
    """
    rect = page.mu_rect
    width = max(rect.width, 1.0)
    height = max(rect.height, 1.0)
    edge_x = width * FRAME_EDGE_TOLERANCE
    edge_y = height * FRAME_EDGE_TOLERANCE
    min_horizontal = width * 0.8
    min_vertical = height * 0.8

    top = bottom = left = right = False
    for x0, y0, x1, y1 in page.iter_line_segments():
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

    # Блок 1: часть CAD-экспортов собирает лист клипом из композиции с
    # координатами за пределами MediaBox — рамка есть визуально, но её
    # линии в path-геометрии «уезжают» за край или лежат вне листа
    # (файл 4400_ОВ, лист 3: линия длиной 9331 pt при ширине листа
    # 1191 pt, x до 9810). Если path-геометрия выходит за пределы
    # листа, рамку достоверно оценить нельзя — не помечаем (fail-open,
    # как растровый штамп).
    offscale = False
    if not (top and bottom and left and right):
        margin = max(rect.width, rect.height) * 0.1
        for x0, y0, x1, y1 in page.iter_line_segments():
            if (
                x0 < rect.x0 - margin or x1 > rect.x1 + margin
                or y0 < rect.y0 - margin or y1 > rect.y1 + margin
            ):
                offscale = True
                break
        if not offscale:
            for box in page.iter_rects():
                if (
                    box.x0 < rect.x0 - margin or box.x1 > rect.x1 + margin
                    or box.y0 < rect.y0 - margin
                    or box.y1 > rect.y1 + margin
                ):
                    offscale = True
                    break
        if offscale:
            return []

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
# «кол.уч»/«№док»/«подп.» — колонки формы 3 (титульные листы).
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
    'кол.уч',
    '№док',
    'подп.',
)

# Минимумы структуры таблицы штампа (тонкие линии, ГОСТ 21.101).
STAMP_MIN_H_LINES = 6
STAMP_MIN_V_LINES = 4

# Блок 1: минимум объектов страницы, целиком лежащих в зоне штампа
# (границы ячеек таблицы штампа лежат внутри зоны, а рамка листа и
# линии чертежа её пересекают). Catch-all для экспортов, где штамп
# нарисован shading-объектами/кривыми, невидимыми для path-фильтра.
STAMP_MIN_INNER_OBJECTS = 6


def _segment_hits_zone(x0, y0, x1, y1, zone) -> bool:
    """Пересечение сегмента с зоной (интервалы; сегменты нулевой толщины)."""
    seg_x0, seg_x1 = min(x0, x1), max(x0, x1)
    seg_y0, seg_y1 = min(y0, y1), max(y0, y1)
    return (
        seg_x1 >= zone.x0 and seg_x0 <= zone.x1
        and seg_y1 >= zone.y0 and seg_y0 <= zone.y1
    )


def _rect_intersection_area(a: Rect, b: Rect) -> float:
    """Площадь пересечения двух оси-выровненных прямоугольников."""
    width = min(a.x1, b.x1) - max(a.x0, b.x0)
    height = min(a.y1, b.y1) - max(a.y0, b.y0)
    if width <= 0 or height <= 0:
        return 0.0
    return width * height


def _zone_labels_via_runs(page, zone: Rect) -> set[str]:
    """Меток штампа по текстовым спанам в зоне (блок 1, fallback).

    GigaCode: iter_line_segments()/iter_rects() видят только top-level
    PATH-объекты; текст же штампа может лежать в Form XObject или
    контент-стриме, а get_text(clip) требует полного попадания границ
    в clip. Спаны из iter_text_runs() перебираются с допуском 5% стороны
    листа: частично выступающий за зону спан «Стадия» — не повод
    для ложного срабатывания.
    """
    labels: set[str] = set()
    rect = page.mu_rect
    loose = Rect(
        zone.x0 - rect.width * 0.05,
        zone.y0 - rect.height * 0.05,
        zone.x1 + rect.width * 0.05,
        zone.y1 + rect.height * 0.05,
    )
    for run in page.iter_text_runs():
        text = ' '.join(
            (run['text'] or '').replace('\xa0', ' ').split(),
        ).casefold()
        if not text:
            continue
        x0, y0, x1, y1 = run['bbox']
        if x1 < loose.x0 or x0 > loose.x1 or y1 < loose.y0 or y0 > loose.y1:
            continue
        for label in STAMP_LABELS:
            if label in text:
                labels.add(label)
    return labels


def _zone_raster_stamp(page, zone: Rect) -> bool:
    """Растровый штамп: изображение закрывает зону (блок 1, fallback).

    Скан листа или штамп «в растр» не дают ни геометрии, ни текста —
    единственный видимый объект в зоне растровое изображение.
    """
    zone_area = zone.width * zone.height
    if zone_area <= 0:
        return False
    for image in page.iter_images():
        overlap = _rect_intersection_area(image, zone)
        if overlap / zone_area >= STAMP_RASTER_COVERAGE:
            return True
    return False


def _displayed_point_to_mu(page, x_disp: float, y_disp: float) -> tuple[float, float]:
    """Точка ОТОБРАЖАЕМОГО листа (пункты) → MU-координаты (блок 1).

    Обратное преобразование к повороту страницы: штамп ищется в правом
    нижнем углу ОТОБРАЖАЕМОГО листа, а геометрия/текст живут в
    неповёрнутой (MU) системе — при /Rotate 90/180/270 угол не совпадает.
    """
    mu = page.mu_rect
    rotation = int(page.rotation or 0) % 360
    if rotation == 90:
        return (y_disp, mu.height - x_disp)
    if rotation == 180:
        return (mu.width - x_disp, mu.height - y_disp)
    if rotation == 270:
        return (mu.width - y_disp, x_disp)
    return (x_disp, y_disp)


def _displayed_zone_to_mu(
    page, x0_frac: float, y0_frac: float, x1_frac: float, y1_frac: float,
) -> Rect:
    """Зона в долях ОТОБРАЖАЕМОГО листа → MU-координаты (блок 1)."""
    disp = page.rect
    ax, ay = _displayed_point_to_mu(
        page, disp.width * x0_frac, disp.height * y0_frac,
    )
    bx, by = _displayed_point_to_mu(
        page, disp.width * x1_frac, disp.height * y1_frac,
    )
    return Rect(
        min(ax, bx), min(ay, by), max(ax, bx), max(ay, by),
    )


def _zone_inner_objects(page, zone: Rect) -> int:
    """Объектов страницы, целиком лежащих в зоне штампа (блок 1).

    Стоп после STAMP_MIN_INNER_OBJECTS — кропотливый полный подсчёт
    не нужен, страница с таблицей штампа даёт сотни объектов внутри
    зоны (4400_ОВ, лист 4: 1299).
    """
    count = 0
    for bounds in page.iter_object_bounds():
        if (
            bounds.x0 >= zone.x0 and bounds.x1 <= zone.x1
            and bounds.y0 >= zone.y0 and bounds.y1 <= zone.y1
        ):
            count += 1
            if count >= STAMP_MIN_INNER_OBJECTS:
                return count
    return count


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

    Бэта-фикс 2026-11-08, блок 1 (ложные срабатывания):
    - зона считается в MU-координатах через поворот страницы — правый
      нижний угол ОТОБРАЖАЕМОГО листа (page.rect зона уезжала);
    - если векторная геометрия штамп не подтверждает, применяются
      fallback: текстовые спаны в зоне (iter_text_runs), растровое
      изображение, закрывающее зону (скан/экспорт в растр), объекты,
      целиком лежащие в зоне (штамп shading-объектами/кривыми), и
      форма 3 титульного листа (штамп в левом нижнем углу).
    """
    rect = page.mu_rect
    zone = _displayed_zone_to_mu(
        page, STAMP_ZONE_X, STAMP_ZONE_Y, 1.0, 1.0,
    )

    text = page.get_text(clip=zone) or ''
    # pdfium может отдавать неразрывные пробелы (\xa0) — нормализуем.
    normalized = ' '.join(text.replace('\xa0', ' ').split()).casefold()
    if 'основная надпись' in normalized:
        return []

    matched_labels = {label for label in STAMP_LABELS if label in normalized}
    if len(matched_labels) >= 2:
        return []

    horizontal = vertical = 0
    attached_box = False
    min_box_width = rect.width * 0.25
    min_box_height = rect.height * 0.03
    max_box_height = rect.height * 0.35
    edge_tolerance = rect.width * 0.05
    # Правый нижний угол ОТОБРАЖАЕМОГО листа в MU-координатах (блок 1):
    # рамка штампа должна примыкать к нему независимо от /Rotate.
    corner_x, corner_y = _displayed_point_to_mu(
        page, page.rect.width, page.rect.height,
    )

    for x0, y0, x1, y1 in page.iter_line_segments():
        if not _segment_hits_zone(x0, y0, x1, y1, zone):
            continue
        if abs(y1 - y0) <= LINE_TOLERANCE_PT and abs(x1 - x0) >= 20:
            horizontal += 1
        elif abs(x1 - x0) <= LINE_TOLERANCE_PT and abs(y1 - y0) >= 20:
            vertical += 1

    for box in page.iter_rects():
        if (
            box.width >= min_box_width
            and min_box_height <= box.height <= max_box_height
            and box.x0 - edge_tolerance <= corner_x <= box.x1 + edge_tolerance
            and box.y0 - edge_tolerance <= corner_y <= box.y1 + edge_tolerance
        ):
            attached_box = True

    if horizontal >= STAMP_MIN_H_LINES and vertical >= STAMP_MIN_V_LINES:
        return []
    if attached_box:
        return []

    # Fallback (блок 1): геометрия штамп не подтверждает — ищем текст
    # спанами, растровое изображение и объекты, целиком лежащие в зоне
    # (штамп, нарисованный shading-объектами/кривыми).
    run_labels = _zone_labels_via_runs(page, zone)
    if len(matched_labels | run_labels) >= 2:
        return []
    if _zone_raster_stamp(page, zone):
        return []

    inner_count = _zone_inner_objects(page, zone)
    if inner_count >= STAMP_MIN_INNER_OBJECTS:
        return []

    # Форма 3 титульного листа (блок 1): основная надпись на титульных
    # листах располагается в ЛЕВОМ нижнем углу. Второй шанс — только
    # текстовые метки (без catch-all по объектам: плотный чертёж слева
    # внизу не должен проходить за штамп).
    left_zone = _displayed_zone_to_mu(
        page, 0.0, STAMP_ZONE_Y, 1 - STAMP_ZONE_X, 1.0,
    )
    left_text = page.get_text(clip=left_zone) or ''
    left_norm = ' '.join(left_text.replace('\xa0', ' ').split()).casefold()
    if 'основная надпись' in left_norm:
        return []
    left_labels = {label for label in STAMP_LABELS if label in left_norm}
    left_labels |= _zone_labels_via_runs(page, left_zone)
    if len(left_labels) >= 2:
        return []

    return [_page_finding(
        page,
        [zone.x0, zone.y0, zone.x1, zone.y1],
        'Не найдена основная надпись (штамп)',
        'major',
        evidence=(
            'В правом нижнем углу нет штампа СПДС '
            f'(надписей: {len(matched_labels | run_labels)}, '
            f'линий: {horizontal}/{vertical}, '
            f'объектов в зоне: {inner_count}; '
            'форма 3 титульного листа (левый угол) тоже не найдена)'
        ),
    )]


def check_font(page) -> list[dict]:
    """SPDS.FONT: минимальная высота шрифта основного текста ≥ 2.5 мм.

    Штамп (правый нижний угол) исключается — его надписи регламентируются
    отдельно; проверяются прозаические текстовые спаны.
    """
    rect = page.mu_rect  # MU-система координат спанов (см. check_frame)
    stamp_x0 = rect.x0 + rect.width * STAMP_ZONE_X
    stamp_y0 = rect.y0 + rect.height * STAMP_ZONE_Y

    findings: list[dict] = []
    for run in page.iter_text_runs():
        text = (run['text'] or '').strip()
        if not text:
            continue
        x0, y0, x1, y1 = run['bbox']
        if x0 >= stamp_x0 and y0 >= stamp_y0:
            continue
        size = float(run['size'] or 0.0)
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


# Реестр правил: RuleDefinition.code → проверка страницы.
RULE_CHECKS: dict[str, Callable] = {
    'SPDS.RAME': check_frame,
    'SPDS.STAMP': check_stamp,
    'SPDS.FONT': check_font,
}
