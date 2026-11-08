"""pypdfium2-обёртка для ai_checks (замена PyMuPDF, лицензия BSD).

Модуль изолирует единственную зависимость от pypdfium2 и эмулирует те
части API PyMuPDF, которые использует ИИ-контур: координаты страницы,
векторные сегменты/прямоугольники, текст с кеглем и координатами,
рендер области в изображение.

Координатные системы (проверено зондами на rot 0/90/180/270 и сдвинутом
MediaBox):
- PyMuPDF ``page.rect``          — отображаемая система (с /Rotate), TL;
- PyMuPDF get_drawings/get_text  — неповёрнутая система, TL (y вниз),
  координаты относительно CropBox;
- pdfium объекты/текст           — неповёрнутое пользовательское
  пространство, BL (y вверх), абсолютные координаты;
- pypdfium2 ``render(rotation=0)`` — рендер КАК ОТОБРАЖАЕМОЙ страницы
  (/Rotate применяется всегда); crop — отступы (left, bottom, right, top)
  в пунктах финального изображения.

Преобразование неповёрнутых координат (поворот страницы не влияет):
``x_mu = x_user - cropbox.x0; y_mu = cropbox.y1 - y_user``.
"""

from __future__ import annotations

import io
import pathlib
import re
from ctypes import byref, c_double, c_float, c_int, c_ubyte

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

__all__ = [
    'PdfiumError',
    'Rect',
    'PdfDocument',
    'PdfPageView',
    'open_pdf',
]

_SEG_MOVETO = pdfium_raw.FPDF_SEGMENT_MOVETO
_SEG_LINETO = pdfium_raw.FPDF_SEGMENT_LINETO
_PAGEOBJ_PATH = pdfium_raw.FPDF_PAGEOBJ_PATH

_FONT_INFO_BUFFER = 4096
# общий буфер для имён шрифтов (выделение на каждый символ дорого)
_FONT_INFO_BUF = (c_ubyte * _FONT_INFO_BUFFER)()


class PdfiumError(RuntimeError):
    """Ошибка разбора/открытия PDF (подкласс RuntimeError — как у PyMuPDF)."""


class Rect:
    """Прямоугольник с интерфейсом pymupdf.Rect (x0/y0/x1/y1, width/height)."""

    __slots__ = ('x0', 'y0', 'x1', 'y1')

    def __init__(self, x0, y0, x1, y1):
        self.x0, self.y0, self.x1, self.y1 = (
            float(x0), float(y0), float(x1), float(y1),
        )

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def is_empty(self) -> bool:
        return self.x1 <= self.x0 or self.y1 <= self.y0

    def __repr__(self):
        return (f'Rect({self.x0:.2f}, {self.y0:.2f}, '
                f'{self.x1:.2f}, {self.y1:.2f})')


# заголовки объектов для авто-ремонта ('N G obj' в потоке файла)
_OBJ_RE = re.compile(rb'(?:^|[\r\n>])(\d{1,10})[\t ]+(\d{1,5})[\t ]+obj\b')


def _read_source(source) -> bytes:
    """Приводит источник к bytes ДО передачи в pypdfium2.

    - str/Path: читаем сами (open(path, 'rb').read() эквивалент
      Path.read_bytes) — данные отдаются через FPDF_LoadMemDocument64,
      нативный FPDF_LoadDocument (и его кодировки пути/второе открытие
      файла) не используется; отсутствующий файл даёт понятное
      исключение с путём.
    - file-like: читается целиком в bytes, поэтому требование pypdfium2
      «file-like должен оставаться открытым до конца работы с
      документом» выполняется автоматически — объект можно закрыть
      сразу после open_pdf (байты удерживаются самим документом).
    - bytes/ctypes.Array — как есть.
    """
    if isinstance(source, (str, pathlib.Path)):
        return pathlib.Path(source).read_bytes()
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    read = getattr(source, 'read', None)
    if read is None:
        return source  # ctypes.Array и пр. — pypdfium2 примет как есть
    data = read()
    if not isinstance(data, (bytes, bytearray)):
        raise PdfiumError(
            'Файл открыт в текстовом режиме — нужен open(path, "rb") '
            f'(получено {type(data).__name__})',
        )
    return bytes(data)


def _object_dict(data: bytes, offset: int) -> bytes:
    """Словарь объекта (до endobj) для поиска /Type."""
    end = data.find(b'endobj', offset)
    if end < 0:
        end = min(offset + 4096, len(data))
    return data[offset:end]


def _rebuild_xref(data: bytes) -> bytes | None:
    """Пересобирает xref-таблицу по фактическому расположению объектов.

    Аналог молчаливого авто-ремонта MuPDF (pdf_repair_xref), который
    выполнялся в прежней реализации на PyMuPDF: сканирует заголовки
    'N G obj', находит /Type /Catalog (если каталога нет, но есть
    /Type /Pages — синтезирует Catalog над ним), дописывает в копию
    файла свежую xref-таблицу и trailer. Возвращает новую копию байтов
    или None, если ремонт невозможен (нет объектов/каталога).
    """
    if data.find(b'%PDF-') < 0:
        return None
    entries: dict[int, tuple[int, int]] = {}
    for match in _OBJ_RE.finditer(data):
        num, gen = int(match.group(1)), int(match.group(2))
        if num not in entries:  # при инкрементальных обновлениях 1-я копия
            entries[num] = (gen, match.start(1))
    if not entries:
        return None
    root = pages_root = None
    for num, (gen, offset) in sorted(entries.items()):
        body = _object_dict(data, offset)
        if root is None and re.search(rb'/Type[\t ]*/Catalog\b', body):
            root = (num, gen)
        if pages_root is None and re.search(rb'/Type[\t ]*/Pages\b', body):
            pages_root = (num, gen)
    if root is None and pages_root is None:
        return None
    max_num = max(entries)
    extra = b''
    if root is None:
        syn_num = max_num + 1
        extra = (
            f'{syn_num} 0 obj\n<< /Type /Catalog /Pages '
            f'{pages_root[0]} {pages_root[1]} R >>\nendobj\n'.encode()
        )
        entries[syn_num] = (0, len(data) + 1)  # сразу после добавляемой \n
        max_num = syn_num
        root = (syn_num, 0)
    out = bytearray(data)
    out.extend(b'\n')
    out.extend(extra)
    xref_off = len(out)
    out.extend(f'xref\n0 {max_num + 1}\n'.encode())
    out.extend(b'0000000000 65535 f \n')
    for i in range(1, max_num + 1):
        if i in entries:
            gen, offset = entries[i]
            out.extend(f'{offset:010d} {gen:05d} n \n'.encode())
        else:
            out.extend(b'0000000000 65535 f \n')
    out.extend(
        f'trailer\n<< /Size {max_num + 1} /Root {root[0]} {root[1]} R >>\n'
        f'startxref\n{xref_off}\n%%EOF\n'.encode()
    )
    return bytes(out)


def _repair_due(exc: Exception) -> bool:
    """Ремонт уместен только для FPDF_ERR_FORMAT (битая структура данных)."""
    if getattr(exc, 'err_code', None) == pdfium_raw.FPDF_ERR_FORMAT:
        return True
    return 'Data format error' in str(exc)  # сборки без err_code


def _reencode_pdf_via_pypdf(data: bytes) -> bytes | None:
    """Переконвертирует PDF через pypdf (fallback для pypdfium2).

    Некоторые PDF (сгенерированные PyMuPDF, специфичными библиотеками)
    содержат байты, которые pypdfium2 не может разобрать, но которые
    PyMuPDF открывает молча. pypdf при чтении и перезаписи «нормализует»
    структуру файла, и pypdfium2 успешно открывает результат.
    """
    try:
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(io.BytesIO(data))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception:  # noqa: BLE001 — fallback: молча возвращаем None
        return None


def open_pdf(source) -> pdfium.PdfDocument:
    """Открывает PDF (путь/bytes/файл); битый файл -> PdfiumError.

    Данные читаются в bytes до передачи в pypdfium2 (см. _read_source):
    str-путь не идёт в нативный FPDF_LoadDocument, file-like не нужно
    держать открытым. Если PDFium не смог разобрать корректно
    прочитанные данные (FPDF_ERR_FORMAT «Data format error» — например,
    у файла нет xref-таблицы из-за обрыва при передаче; PyMuPDF такие
    файлы открывал молча), выполняется пересборка xref (_rebuild_xref)
    и повторная попытка открытия восстановленной копии.
    
    Если ремонт xref не помог — пробуем переконвертацию через pypdf
    (нормализует структуру файла, которую pypdfium2 не понимает).
    """
    data = _read_source(source)
    
    # Попытка 1: прямой открытие.
    try:
        return pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        # Попытка 2: ремонт xref (если ошибка формата).
        if data and _repair_due(exc):
            try:
                repaired = _rebuild_xref(data)
                if repaired is not None:
                    return pdfium.PdfDocument(repaired)
            except Exception:  # noqa: BLE001
                pass
    
    # Попытка 3: переконвертация через pypdf (fallback).
    reencoded = _reencode_pdf_via_pypdf(data)
    if reencoded is not None:
        try:
            return pdfium.PdfDocument(reencoded)
        except pdfium.PdfiumError:
            pass
    
    raise PdfiumError(f'Не удалось открыть PDF')


class PdfPageView:
    """Обёртка страницы pdfium с интерфейсом, необходимым правилам и рендеру.

    Экземпляр привязан к открытому PdfDocument; закрывается через close()
    (текстовая страница освобождается раньше документа).
    """

    def __init__(self, doc: pdfium.PdfDocument, index: int):
        self._doc = doc
        self._index = index
        self._page = doc[index]
        self._cropbox = self._page.get_cropbox()  # (l, b, r, t)
        self._textpage = None

    @property
    def number(self) -> int:
        """Индекс страницы с 0 (как page.number в PyMuPDF)."""
        return self._index

    @property
    def rect(self) -> Rect:
        """Отображаемый прямоугольник (как page.rect PyMuPDF), с /Rotate."""
        return Rect(0, 0, self._page.get_width(), self._page.get_height())

    @property
    def rotation(self) -> int:
        return self._page.get_rotation()

    @property
    def mediabox(self) -> Rect:
        """MediaBox (как page.mediabox PyMuPDF): неповёрнутый, BL-система."""
        left, bottom, right, top = self._page.get_mediabox()
        return Rect(left, bottom, right, top)

    @property
    def mu_rect(self) -> Rect:
        """Прямоугольник страницы в MU-координатах (система геометрии правил).

        Бэта-фикс 2026-11-08, блок 1 (SPDS.STAMP/RAME): правила сравнивают
        геометрию iter_line_segments()/iter_rects()/get_text(clip), которая
        отдаётся в неповёрнутой TL-системе относительно CropBox, а прежний
        page.rect — отображаемая (с /Rotate) система. При /Rotate 90/270
        они подменяют стороны и зона штампа уезжала.

        page.mediabox — BL-система с абсолютными координатами, поэтому
        в правилах используется именно MU-прямоугольник: для MediaBox с
        нулевым началом ширина/высота совпадают с MediaBox, но оси — как
        у геометрии (y вниз). См. преобразование _to_mu().
        """
        left, bottom, right, top = self._cropbox
        return Rect(0.0, 0.0, right - left, top - bottom)

    def close(self):
        if self._textpage is not None:
            self._textpage.close()
            self._textpage = None
        self._page.close()

    # --- координаты ---------------------------------------------------------

    def _to_mu(self, x_user: float, y_user: float) -> tuple[float, float]:
        """pdfium user (BL) -> pymupdf unrotated TL (cropbox-relative)."""
        return (x_user - self._cropbox[0], self._cropbox[3] - y_user)

    def _clip_to_user(self, clip: Rect) -> tuple[float, float, float, float]:
        """Прямоугольник в MU-координатах -> (left, bottom, right, top) user."""
        return (
            clip.x0 + self._cropbox[0],
            self._cropbox[3] - clip.y1,
            clip.x1 + self._cropbox[0],
            self._cropbox[3] - clip.y0,
        )

    # --- векторная геометрия --------------------------------------------------

    def iter_line_segments(self):
        """Линейные сегменты страницы в координатах PyMuPDF (TL, неповёрнутые).

        Аналог обхода page.get_drawings() с разбором items 'l'/'re':
        пути pdfium раскрываются в рёбра через moveto/lineto/close,
        прямоугольник 're' даёт те же 4 ребра.
        """
        for obj in self._page.get_objects(filter=[_PAGEOBJ_PATH], max_depth=15):
            count = pdfium_raw.FPDFPath_CountSegments(obj)
            current: tuple[float, float] | None = None
            for i in range(count):
                seg = pdfium_raw.FPDFPath_GetPathSegment(obj, i)
                x, y = c_float(), c_float()
                pdfium_raw.FPDFPathSegment_GetPoint(seg, x, y)
                seg_type = pdfium_raw.FPDFPathSegment_GetType(seg)
                is_close = pdfium_raw.FPDFPathSegment_GetClose(seg)
                point = (x.value, y.value)
                if current is not None:
                    yield self._segment_mu(current, point)
                if seg_type == _SEG_MOVETO and not is_close:
                    current = None if is_close else point
                else:
                    current = point
            obj.close()

    def iter_rects(self):
        """Оси-выровненные прямоугольники страницы (аналог items 're').

        Замкнутая подфигурация из 4 углов с горизонтальными/вертикальными
        рёбрами распознаётся как прямоугольник (оператор 're' в PDF).
        """
        for obj in self._page.get_objects(filter=[_PAGEOBJ_PATH], max_depth=15):
            count = pdfium_raw.FPDFPath_CountSegments(obj)
            points: list[tuple[float, float]] = []
            closed = False
            for i in range(count):
                seg = pdfium_raw.FPDFPath_GetPathSegment(obj, i)
                x, y = c_float(), c_float()
                pdfium_raw.FPDFPathSegment_GetPoint(seg, x, y)
                seg_type = pdfium_raw.FPDFPathSegment_GetType(seg)
                is_close = pdfium_raw.FPDFPathSegment_GetClose(seg)
                if seg_type == _SEG_MOVETO and not is_close and points:
                    rect = _closed_rect(points, closed)
                    if rect is not None:
                        yield rect
                    points = []
                    closed = False
                points.append(self._to_mu(x.value, y.value))
                if is_close:
                    closed = True
                    rect = _closed_rect(points, closed)
                    if rect is not None:
                        yield rect
                    points = []
                    closed = False
            if points:
                rect = _closed_rect(points, closed)
                if rect is not None:
                    yield rect
            obj.close()

    def _segment_mu(self, a, b):
        x0, y0 = self._to_mu(*a)
        x1, y1 = self._to_mu(*b)
        return (x0, y0, x1, y1)

    def iter_images(self):
        """Растровые объекты страницы в MU-координатах (FPDFPageObj_GetBounds).

        Бэта-фикс 2026-11-08, блок 1: штамп может быть растровым (скан
        листа, экспорт «штампа в растр») — path-объекты и текст его не
        видят. Границы объекта отдаются в неповёрнутом user-пространстве
        (BL, абсолютные) — конвертируются в MU, как сегменты путей.
        """
        for obj in self._page.get_objects(
            filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE], max_depth=15,
        ):
            left, bottom, right, top = (
                c_float(), c_float(), c_float(), c_float(),
            )
            if pdfium_raw.FPDFPageObj_GetBounds(
                obj, byref(left), byref(bottom), byref(right), byref(top),
            ):
                x0, y1 = self._to_mu(left.value, bottom.value)
                x1, y0 = self._to_mu(right.value, top.value)
                yield Rect(
                    min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1),
                )
            obj.close()

    def iter_object_bounds(self):
        """Границы ВСЕХ top-level объектов страницы в MU-координатах.

        Бэта-фикс 2026-11-08, блок 1: часть CAD-экспортов рисует весь
        лист shading-объектами/кривыми (файл 4400_ОВ из бэта-теста:
        14 447 shading-объектов, path — ноль), поэтому path-фильтр
        get_objects() и текстовый слой штамп не видят. Здесь просто
        перечисляются границы объектов через FPDFPageObj_GetBounds
        (user BL → MU TL), без разбора геометрии — для подсчёта объектов,
        целиком лежащих в зоне штампа.
        """
        page_raw = self._page.raw
        for index in range(pdfium_raw.FPDFPage_CountObjects(page_raw)):
            obj = pdfium_raw.FPDFPage_GetObject(page_raw, index)
            left, bottom, right, top = (
                c_float(), c_float(), c_float(), c_float(),
            )
            if pdfium_raw.FPDFPageObj_GetBounds(
                obj, byref(left), byref(bottom), byref(right), byref(top),
            ):
                x0, y1 = self._to_mu(left.value, bottom.value)
                x1, y0 = self._to_mu(right.value, top.value)
                yield Rect(
                    min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1),
                )

    # --- текст ------------------------------------------------------------------

    def _ensure_textpage(self):
        if self._textpage is None:
            self._textpage = self._page.get_textpage()
        return self._textpage

    def get_text(self, *, clip: Rect | None = None) -> str:
        """Текст страницы (аналог page.get_text('text') PyMuPDF).

        clip — прямоугольник в MU-координатах: отдаётся текст, попавший
        в область (нативный FPDFText_GetBoundedText, с переносами строк).
        """
        tp = self._ensure_textpage()
        if clip is None:
            return _normalize_text(tp.get_text_range())
        left, bottom, right, top = self._clip_to_user(clip)
        return _normalize_text(
            tp.get_text_bounded(left=left, bottom=bottom, right=right, top=top),
        )

    def iter_text_chars(self):
        """Символы страницы: (index, char, size, box_mu, origin_mu).

        box_mu — loose box (с выносными, как bbox спана PyMuPDF),
        origin_mu — базовая линия (лево, baseline) в координатах PyMuPDF.
        """
        tp = self._ensure_textpage()
        count = tp.count_chars()
        for i in range(count):
            code = pdfium_raw.FPDFText_GetUnicode(tp, i)
            char = chr(code) if code else ''
            size = float(pdfium_raw.FPDFText_GetFontSize(tp, i))
            rectf = pdfium_raw.FS_RECTF()
            if not pdfium_raw.FPDFText_GetLooseCharBox(tp, i, byref(rectf)):
                continue
            ox, oy = c_double(), c_double()
            if not pdfium_raw.FPDFText_GetCharOrigin(tp, i, ox, oy):
                continue
            x0, y1 = self._to_mu(rectf.left, rectf.bottom)
            x1, y0 = self._to_mu(rectf.right, rectf.top)
            ox_mu, oy_mu = self._to_mu(ox.value, oy.value)
            yield (i, char, size, (x0, y0, x1, y1), (ox_mu, oy_mu))

    def iter_text_runs(self):
        """«Спаны» PyMuPDF-подобного вида из последовательных символов.

        Последовательные символы с одинаковой базовой линией, кеглем и
        шрифтом образуют спан: {text, size, bbox, origin} — как элементы
        span в get_text('dict') PyMuPDF.
        """
        runs: list[dict] = []
        state: dict | None = None
        tp = self._ensure_textpage()
        for i, char, size, box, origin in self.iter_text_chars():
            if char == '' or ord(char) in (0x000A, 0x000D):
                if state is not None:
                    runs.append(_finish_run(state))
                    state = None
                continue
            key = (
                round(origin[1], 1),      # базовая линия строки
                round(size, 2),           # кегль
                _char_font_name(tp, i),   # шрифт
            )
            if state is not None and state['key'] != key:
                runs.append(_finish_run(state))
                state = None
            if state is None:
                state = {
                    'key': key,
                    'text': [],
                    'size': size,
                    'origin': origin,
                    'x0': box[0], 'y0': box[1], 'x1': box[2], 'y1': box[3],
                }
            state['text'].append(char)
            state['x0'] = min(state['x0'], box[0])
            state['y0'] = min(state['y0'], box[1])
            state['x1'] = max(state['x1'], box[2])
            state['y1'] = max(state['y1'], box[3])
        if state is not None:
            runs.append(_finish_run(state))
        return runs

    # --- рендер ---------------------------------------------------------------

    def render_clip(self, clip: Rect, scale: float = 2.0):
        """Рендер области clip в PIL.Image (RGB).

        Эквивалент page.get_pixmap(clip=clip, matrix=Matrix(scale, scale)):
        clip задаётся в координатах page.rect (отображаемая система),
        /Rotate применяется рендерером автоматически.
        """
        rect = self.rect
        crop = (
            max(0.0, clip.x0 - rect.x0),
            max(0.0, rect.y1 - clip.y1),
            max(0.0, rect.x1 - clip.x1),
            max(0.0, clip.y0 - rect.y0),
        )
        bitmap = self._page.render(scale=scale, rotation=0, crop=crop)
        image = bitmap.to_pil()
        bitmap.close()
        if image.mode != 'RGB':
            image = image.convert('RGB')
        return image

    def render_jpeg_clip(self, clip: Rect, scale: float = 2.0,
                         quality: int = 95) -> bytes:
        """JPEG-байты области clip (эквивалент pixmap.tobytes('jpeg'))."""
        image = self.render_clip(clip, scale=scale)
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=quality)
        return buffer.getvalue()


def _finish_run(state: dict) -> dict:
    return {
        'text': ''.join(state['text']),
        'size': state['size'],
        'bbox': (state['x0'], state['y0'], state['x1'], state['y1']),
        'origin': state['origin'],
    }


def _closed_rect(points, closed):
    """Замкнутая подфигурация из 4 углов с осями-выровненными рёбрами -> Rect.

    points — вершины подфигурации в MU-координатах (после _to_mu).
    """
    if not closed or len(points) < 5:
        return None
    # уникальные вершины: убираем последовательные повторы и замыкающий
    corners = [points[0]]
    for point in points[1:]:
        if (abs(point[0] - corners[-1][0]) > 0.01
                or abs(point[1] - corners[-1][1]) > 0.01):
            corners.append(point)
    if (len(corners) > 1
            and abs(corners[0][0] - corners[-1][0]) <= 0.01
            and abs(corners[0][1] - corners[-1][1]) <= 0.01):
        corners.pop()
    if len(corners) != 4:
        return None
    xs = sorted({round(point[0], 2) for point in corners})
    ys = sorted({round(point[1], 2) for point in corners})
    if len(xs) != 2 or len(ys) != 2:
        return None
    return Rect(xs[0], ys[0], xs[1], ys[1])


def _char_font_name(tp, index: int) -> str:
    """Имя шрифта символа (FPDFText_GetFontInfo)."""
    flags = c_int()
    length = pdfium_raw.FPDFText_GetFontInfo(
        tp, index, _FONT_INFO_BUF, _FONT_INFO_BUFFER, byref(flags),
    )
    if length <= 0:
        return ''
    raw_bytes = bytes(_FONT_INFO_BUF[:length - 1]) if length > 1 else b''
    return raw_bytes.decode('latin-1', errors='replace')


def _normalize_text(text: str) -> str:
    """\\r\\n -> \\n; непустая страница PyMuPDF завершается \\n."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if text and not text.endswith('\n'):
        text += '\n'
    return text
