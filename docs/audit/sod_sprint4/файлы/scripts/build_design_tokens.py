#!/usr/bin/env python3
"""Генератор дизайн-токенов СОД (Stream-CAD).

Единый источник правды — static/design-tokens/tokens.json + корневой urls.py.
Скрипт порождает четыре артефакта, которые коммитятся в репозиторий:

  1. static/design-tokens/tokens.css          — CSS-переменные + семантические классы
  2. static/js/design-tokens.js               — window.CDE_TOKENS (цвета статусов для JS)
  3. storage/domain/design_colors.py          — константы для Python (вкл. PyMuPDF RGB)
  4. static/js/sw-bypass-list.js              — bypass-префиксы сервис-воркера (Спринт 3)

Использование:
    python scripts/build_design_tokens.py           # перегенерировать файлы
    python scripts/build_design_tokens.py --check   # проверка дрейфа (exit 1, если
                                                     # артефакты не совпадают с источниками)

Правило: НИКТО не редактирует сгенерированные файлы руками — только tokens.json,
urls.py и этот шаблонизатор. CI (.github/workflows/design-tokens.yml) ловит дрейф.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOKENS_JSON = REPO_ROOT / 'static' / 'design-tokens' / 'tokens.json'
SW_BYPASS_JS = REPO_ROOT / 'static' / 'js' / 'sw-bypass-list.js'
OUTPUTS = (
    REPO_ROOT / 'static' / 'design-tokens' / 'tokens.css',
    REPO_ROOT / 'static' / 'js' / 'design-tokens.js',
    REPO_ROOT / 'storage' / 'domain' / 'design_colors.py',
    SW_BYPASS_JS,
)

GENERATED_NOTE_CSS = (
    '/* ==========================================================================\n'
    '   АВТОГЕНЕРАТ из design-tokens/tokens.json — НЕ РЕДАКТИРОВАТЬ ВРУЧНУЮ.\n'
    '   Правки вносить в tokens.json, затем: python scripts/build_design_tokens.py\n'
    '   ========================================================================== */'
)
GENERATED_NOTE_JS = (
    '/* АВТОГЕНЕРАТ из design-tokens/tokens.json — НЕ РЕДАКТИРОВАТЬ ВРУЧНУЮ.\n'
    '   Правки вносить в tokens.json, затем: python scripts/build_design_tokens.py */'
)
GENERATED_NOTE_PY = (
    '"""АВТОГЕНЕРАТ из static/design-tokens/tokens.json — не редактировать вручную.\n'
    '\n'
    'Правки вносить в tokens.json, затем: python scripts/build_design_tokens.py\n'
    '"""'
)
GENERATED_NOTE_SW = (
    '/* АВТОГЕНЕРАТ из urls.py (Спринт 3, P2) — НЕ РЕДАКТИРОВАТЬ ВРУЧНУЮ.\n'
    '   Новый маршрут добавили в urls.py — перегенерируйте:\n'
    '   python scripts/build_design_tokens.py (CI ловит дрейф). */'
)


def load_tokens() -> dict:
    with TOKENS_JSON.open(encoding='utf-8') as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# tokens.css
# ---------------------------------------------------------------------------

def _var(name: str, value: str, comment: str = '') -> str:
    line = '  --{name}: {value};'.format(name=name, value=value)
    if comment:
        line += '  /* {comment} */'.format(comment=comment)
    return line


def render_css(tokens: dict) -> str:
    color = tokens['color']
    surface = color['surface']
    border = color['border']
    text = color['text']
    accent = color['accent']
    statuses = color['status']
    typo = tokens['typography']
    scale = typo['scale']
    spacing = tokens['spacing']
    controls = tokens['controls']
    radius = tokens['radius']
    shadow = tokens['shadow']
    motion = tokens['motion']
    status_map = tokens['status_map']

    lines: list[str] = []
    add = lines.append

    add(GENERATED_NOTE_CSS)
    add('/*')
    add('   CDE «Среда общих данных» — дизайн-токены (CSS Custom Properties)')
    add('   Источник истины: tokens.json · Версия {version}'.format(version=tokens['version']))
    add('   ----------------------------------------------------------------------------')
    add('   Подключение:  <link rel="stylesheet" href="tokens.css">')
    add('   Шрифты — самолокальные (Спринт 3, P1): static/fonts/fonts.css,')
    add('   CDN отключён ради офлайна на площадке.')
    add('')
    add('   ГЛАВНЫЕ ПРАВИЛА СИСТЕМЫ')
    add('   1. Один смысл — один цвет. Статусные цвета одинаковы в панели замечаний,')
    add('      в бейджах таблиц и в маркерах на листе документа.')
    add('   2. Акцентный цвет ({accent}) — единственный. Не красить им статусы.'.format(accent=accent['solid']))
    add('   3. Статус никогда не кодируется только цветом: всегда иконка + текст.')
    add('   4. Цвет + контекст применения:')
    add('        solid   — маркеры, иконки, точки')
    add('        subtle  — фон бейджа/строки   (текст на нём — on, контраст AA)')
    add('   5. Формат дат и версий: 30.10.2026, 13:41 · v1 · R6 · tabular-nums.')
    add('   ========================================================================== */')
    add('')
    add(':root {')
    add('  /* --- Поверхности --- */')
    add(_var('c-bg-app', surface['app']))
    add(_var('c-bg-surface', surface['surface']))
    add(_var('c-bg-surface-hover', surface['surface-hover']))
    add(_var('c-bg-surface-sunken', surface['surface-sunken']))
    add(_var('c-bg-canvas', surface['canvas'], 'тёмный canvas вьювера'))
    add(_var('c-bg-header', surface['header'], 'графитовый хедер'))
    add(_var('c-bg-header-hover', surface.get('header-hover', 'rgba(255, 255, 255, 0.08)'), 'ховер ghost-кнопок на тёмном'))
    add(_var('c-bg-row-hover', surface['row-hover'], 'ховер кликабельной строки'))
    add('')
    add('  /* --- Границы --- */')
    add(_var('c-border-hairline', border['hairline']))
    add(_var('c-border-default', border['default']))
    add(_var('c-border-header', border['header'], 'граница графитового хедера'))
    add(_var('c-border-header-control', border.get('header-control', '#4B5563'), 'рамки ghost-кнопок на хедере'))
    add('')
    add('  /* --- Текст --- */')
    add(_var('c-text-primary', text['primary']))
    add(_var('c-text-secondary', text['secondary']))
    add(_var('c-text-muted', text['muted']))
    add(_var('c-text-on-dark', text['on-dark']))
    add(_var('c-text-on-dark-muted', text['on-dark-muted']))
    add(_var('c-text-inverse', text['inverse']))
    add('')
    add('  /* --- Акцент (единственный) --- */')
    add(_var('c-accent', accent['solid']))
    add(_var('c-accent-hover', accent['hover']))
    add(_var('c-accent-pressed', accent['pressed']))
    add(_var('c-accent-subtle', accent['subtle']))
    add(_var('c-accent-on-subtle', accent['on-subtle']))
    add(_var('c-accent-on-header', accent.get('on-header', '#9FB6DF'), 'акцент-ссылки на графитовом хедере'))
    add(_var('c-accent-on-header-strong', accent.get('on-header-strong', '#CBD8EF'), 'ховер акцента на хедере'))
    add('')
    add('  /* --- Статусы: solid / subtle / on --- */')
    for name, values in statuses.items():
        add('  --s-{name}-solid: {solid};  --s-{name}-subtle: {subtle};  --s-{name}-on: {on}; /* {comment} */'.format(
            name=name,
            solid=values['solid'],
            subtle=values['subtle'],
            on=values['on'],
            comment=values['comment'],
        ))
    add('')
    add('  /* --- Типографика --- */')
    add(_var('font-sans', typo['sans']))
    add(_var('font-mono', typo['mono']))
    add('')
    for token_name, spec in scale.items():
        add(_var(
            'text-{name}'.format(name=token_name),
            spec['size'],
            spec['comment'],
        ))
        add(_var('lh-{name}'.format(name=token_name), spec['line']))
    add('')
    add('  /* --- Сетка 8pt --- */')
    add('  ' + ' '.join(
        '--sp-{key}: {value};'.format(key=key, value=value)
        for key, value in spacing.items()
    ))
    add('')
    add('  /* --- Контролы и плотность --- */')
    add('  ' + ' '.join(
        '--{key}: {value};'.format(key=key, value=value)
        for key, value in controls.items()
    ))
    add('')
    add('  /* --- Радиусы --- */')
    add('  ' + ' '.join(
        '--r-{key}: {value};'.format(key=key, value=value)
        for key, value in radius.items()
    ))
    add('')
    add('  /* --- Тени --- */')
    for key, value in shadow.items():
        add(_var('shadow-{key}'.format(key=key), value))
    add('')
    add('  /* --- Моушн --- */')
    add('  ' + ' '.join(
        '--dur-{key}: {value};'.format(key=key, value=value)
        for key, value in motion.items()
        if key != 'ease-out'
    ))
    add(_var('ease-out', motion['ease-out']))
    add('')
    add('  /* --- Фокус (не отключать!) --- */')
    add(_var('focus-ring', tokens['focus']['ring']))
    add('}')

    # --- Алиасы статусов: коды моделей -> семантические токены ---
    add('')
    add('/* ---------------------------------------------------------------------------')
    add('   Алиасы статусов: код из модели (Issue / Document / Project) -> токен.')
    add('   Позволяют писать var(--status-{{ status|lower }}-bg) прямо в шаблонах.')
    add('   ------------------------------------------------------------------------- */')
    add(':root {')
    seen: set[str] = set()
    for entity in ('issue', 'document', 'project'):
        for code, semantic in status_map[entity].items():
            if code in seen:
                continue
            seen.add(code)
            add('  --status-{code}: var(--s-{semantic}-solid);'.format(code=code.lower(), semantic=semantic))
            add('  --status-{code}-bg: var(--s-{semantic}-subtle);'.format(code=code.lower(), semantic=semantic))
            add('  --status-{code}-on: var(--s-{semantic}-on);'.format(code=code.lower(), semantic=semantic))
    add('}')

    # --- Тёмная тема ---
    dark = color['dark']
    add('')
    add('/* ---------------------------------------------------------------------------')
    add('   Тёмная тема: применяется атрибутом  <html data-theme="dark">')
    add('   Рекомендуется для вьювера при работе с документом > 1 часа.')
    add('   ------------------------------------------------------------------------- */')
    add('[data-theme="dark"] {')
    add('  ' + _var('c-bg-app', dark['surface']['app']))
    add('  ' + _var('c-bg-surface', dark['surface']['surface']))
    add('  ' + _var('c-bg-surface-hover', dark['surface']['surface-hover']))
    add('  ' + _var('c-bg-surface-sunken', dark['surface']['surface-sunken']))
    add('  ' + _var('c-border-hairline', dark['border']['hairline']))
    add('  ' + _var('c-border-default', dark['border']['default']))
    add('  ' + _var('c-text-primary', dark['text']['primary']))
    add('  ' + _var('c-text-secondary', dark['text']['secondary']))
    add('  ' + _var('c-text-muted', dark['text']['muted']))
    add('  ' + _var('c-accent', dark['accent']['solid']))
    add('  ' + _var('c-accent-hover', dark['accent']['hover']))
    add('  ' + _var('c-accent-subtle', dark['accent']['subtle']))
    add('  ' + _var('c-accent-on-subtle', dark['accent']['on-subtle']))
    for key, value in dark['shadow'].items():
        add('  ' + _var('shadow-{key}'.format(key=key), value))
    add('}')

    # --- Семантические классы ---
    add('')
    add('/* ---------------------------------------------------------------------------')
    add('   Готовые семантические классы')
    add('   ------------------------------------------------------------------------- */')
    add('')
    add('/* Статусный бейдж: <span class="cde-badge cde-badge--attention">Не принято</span>')
    add('   Иконку добавлять обязательным префиксом (SVG/шрифтовой), не только цветом */')
    add('.cde-badge {')
    add('  display: inline-flex; align-items: center; gap: 6px;')
    add('  padding: 2px 10px;')
    add('  border-radius: var(--r-pill);')
    add('  font: 500 var(--text-caption)/var(--lh-caption) var(--font-sans);')
    add('  white-space: nowrap;')
    add('}')
    for name in statuses:
        add('.cde-badge--{name} {{ background: var(--s-{name}-subtle); color: var(--s-{name}-on); }}'.format(name=name))
    add('.cde-badge--accent    { background: var(--c-accent-subtle);    color: var(--c-accent-on-subtle); }')
    add('')

    add('/* Классы по кодам статусов (совместимость с моделями):')
    add('   {% if project.status == \'active\' %}cde-badge--active{% endif %} и т.п. */')
    seen = set()
    for entity in ('issue', 'document', 'project'):
        for code, semantic in status_map[entity].items():
            if code in seen:
                continue
            seen.add(code)
            add('.cde-badge--{code} {{ background: var(--s-{semantic}-subtle); color: var(--s-{semantic}-on); }}'.format(code=code.lower(), semantic=semantic))
    add('')

    add('/* Дедлайн: живой индикатор срока ответа */')
    add('.cde-deadline { background: var(--s-wait-subtle); color: var(--s-wait-on); }')
    add('.cde-deadline--soon     { background: var(--s-review-subtle); color: var(--s-review-on); } /* <= 3 дней */')
    add('.cde-deadline--overdue  { background: var(--s-attention-solid);  color: var(--c-text-inverse); } /* просрочено */')
    add('')
    add('/* Чипы версий: <span class="cde-chip">v1</span> */')
    add('.cde-chip {')
    add('  display: inline-flex; align-items: center;')
    add('  padding: 1px 8px;')
    add('  border: 1px solid var(--c-border-hairline);')
    add('  border-radius: var(--r-sm);')
    add('  font: 500 var(--text-caption)/var(--lh-caption) var(--font-mono);')
    add('  color: var(--c-text-secondary);')
    add('  font-variant-numeric: tabular-nums;')
    add('}')
    add('')
    add('/* Маркер замечания на листе: цвет = СТАТУС, не номер */')
    add('.cde-marker {')
    add('  display: inline-flex; align-items: center; justify-content: center;')
    add('  width: 24px; height: 24px;')
    add('  border-radius: var(--r-pill);')
    add('  color: var(--c-text-inverse);')
    add('  font: 600 12px/1 var(--font-mono);')
    add('  box-shadow: var(--shadow-2);')
    add('  cursor: pointer;')
    add('  font-variant-numeric: tabular-nums;')
    add('}')
    for name in statuses:
        add('.cde-marker[data-status="{name}"] {{ background: var(--s-{name}-solid); }}'.format(name=name))
    add('.cde-marker.is-active { outline: 3px solid var(--c-text-inverse); outline-offset: 1px; }')
    add('')
    add('/* Табличные данные: даты/версии не «пляшут» */')
    add('.cde-tnum { font-variant-numeric: tabular-nums; }')
    add('')
    add('/* Клавиатурный фокус */')
    add(':focus-visible { outline: none; box-shadow: var(--focus-ring); }')
    add('')
    add('/* Уважение к prefers-reduced-motion */')
    add('@media (prefers-reduced-motion: reduce) {')
    add('  *, *::before, *::after { transition-duration: 0.01ms !important; animation-duration: 0.01ms !important; }')
    add('}')
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# design-tokens.js
# ---------------------------------------------------------------------------

def render_js(tokens: dict) -> str:
    status_map = tokens['status_map']['issue']
    statuses = tokens['color']['status']

    hex_entries = []
    class_entries = []
    for code in sorted(status_map):
        semantic = status_map[code]
        values = statuses[semantic]
        hex_entries.append(
            "            {code}: '{solid}',".format(code=code, solid=values['solid'])
        )
        class_entries.append(
            "            {code}: 'cde-badge cde-badge--{semantic}',".format(
                code=code, semantic=semantic,
            )
        )

    return '\n'.join([
        GENERATED_NOTE_JS,
        '/* Подключать ДО pdf_viewer.js: цвета статусов замечаний берутся отсюда,',
        '   чтобы бейдж в панели, маркер на листе и рамка в экспорте PDF совпадали. */',
        '(function () {',
        "    'use strict';",
        '',
        '    window.CDE_TOKENS = {',
        "        version: '{version}',".format(version=tokens['version']),
        '        /* HEX-цвет маркера по коду статуса (для canvas и рамок) */',
        '        statusHex: {',
        *hex_entries,
        '        },',
        '        /* CSS-класс бейджа по коду статуса */',
        '        statusClass: {',
        *class_entries,
        '        },',
        "        defaultStatus: 'NEW'",
        '    };',
        '})();',
    ]) + '\n'


# ---------------------------------------------------------------------------
# storage/domain/design_colors.py
# ---------------------------------------------------------------------------

def render_py(tokens: dict) -> str:
    status_map = tokens['status_map']['issue']
    statuses = tokens['color']['status']
    excel = tokens.get('excel', {})

    def hex_to_rgb01(hex_value: str) -> tuple[float, float, float]:
        value = hex_value.lstrip('#')
        return tuple(round(int(value[index:index + 2], 16) / 255, 2) for index in (0, 2, 4))

    hex_entries = []
    rgb_entries = []
    class_entries = []
    for code in sorted(status_map):
        semantic = status_map[code]
        values = statuses[semantic]
        rgb = hex_to_rgb01(values['solid'])
        hex_entries.append(
            "    '{code}': '{solid}',".format(code=code, solid=values['solid'])
        )
        rgb_entries.append(
            "    '{code}': ({r}, {g}, {b}),  # {hex}".format(
                code=code, r=rgb[0], g=rgb[1], b=rgb[2], hex=values['solid'],
            )
        )
        class_entries.append(
            "    '{code}': 'cde-badge cde-badge--{semantic}',".format(code=code, semantic=semantic)
        )

    return '\n'.join([
        GENERATED_NOTE_PY,
        '',
        'TOKENS_VERSION = {version!r}'.format(version=tokens['version']),
        'DEFAULT_STATUS = {default!r}'.format(default='NEW'),
        '',
        '# HEX-цвета статусов замечаний (совпадают с tokens.css и design-tokens.js).',
        'ISSUE_STATUS_HEX = {',
        *hex_entries,
        '}',
        '',
        '# RGB 0..1 для PyMuPDF (экспорт PDF с маркапами): services.PDF_ISSUE_COLORS.',
        'ISSUE_STATUS_RGB = {',
        *rgb_entries,
        '}',
        '',
        '# CSS-классы бейджей для шаблонов и экспорта.',
        'ISSUE_STATUS_CLASS = {',
        *class_entries,
        '}',
        '',
        '# Цвета Excel-экспорта (не входят в CSS/JS).',
        '# Спринт 3: без «#» — openpyxl требует aRGB-строку, иначе export падает.',
        'EXCEL_HEADER_FILL = {value!r}'.format(
            value=excel.get('header_fill', '#EFEFEC').lstrip('#')
        ),
        'EXCEL_OVERDUE_TEXT = {value!r}'.format(
            value=excel.get('overdue_text', '#B02A37').lstrip('#')
        ),
        'EXCEL_LINK = {value!r}'.format(
            value=excel.get('link', '#0563C1').lstrip('#')
        ),
    ]) + '\n'


# ---------------------------------------------------------------------------
# sw-bypass-list.js (Спринт 3, P2)
# ---------------------------------------------------------------------------

def render_sw_bypass() -> str:
    """Собирает префиксы динамических маршрутов из корневого URLConf.

    Раньше список «что не кэшировать» был зашит руками в sw.js и дрейфовал
    с urls.py при каждом новом маршруте. Теперь service worker подключает
    этот файл через importScripts: любой маршрут, объявленный в urls.py,
    автоматически попадает в «network-only». Медиа добавляются явно
    (MEDIA_URL может не попасть в urlconf), сам /sw.js исключается.
    """
    import os
    import sys

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sod_core.settings')
    import django

    django.setup()
    from django.urls import get_resolver

    prefixes: set[str] = set()

    def _head(pattern_obj) -> str:
        """Первый сегмент маршрута (Django 6: текст маршрута — str(pattern)).

        Регулярные маршруты (re_path) и параметризованные корни ('<int:pk>/')
        не являются литеральными префиксами — пропускаем их.
        """
        text = str(pattern_obj) if pattern_obj is not None else ''
        head = text.split('/', 1)[0]
        if not re.fullmatch(r'[A-Za-z0-9_-]+', head):
            return ''
        return head

    def walk(patterns) -> None:
        for pattern in patterns:
            head = _head(getattr(pattern, 'pattern', None))
            if head and head != 'sw.js':
                prefixes.add('/{head}/'.format(head=head))
            if hasattr(pattern, 'url_patterns'):
                walk(pattern.url_patterns)

    walk(get_resolver().url_patterns)
    prefixes.add('/media/')

    lines = [
        GENERATED_NOTE_SW,
        '/* Префиксы, которые сервис-воркер НЕ кэширует (network-only). */',
        '(function () {',
        "    'use strict';",
        '    self.SOD_SW_BYPASS = [',
    ]
    for prefix in sorted(prefixes):
        lines.append("        '{prefix}',".format(prefix=prefix))
    lines += ['    ];', '})();']
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_all(tokens: dict) -> dict[Path, str]:
    return {
        OUTPUTS[0]: render_css(tokens),
        OUTPUTS[1]: render_js(tokens),
        OUTPUTS[2]: render_py(tokens),
        OUTPUTS[3]: render_sw_bypass(),
    }


def main() -> int:
    check_only = '--check' in sys.argv[1:]
    tokens = load_tokens()
    rendered = build_all(tokens)

    if check_only:
        drifted = []
        for path, content in rendered.items():
            if not path.exists():
                drifted.append('{path} (отсутствует)'.format(path=path.relative_to(REPO_ROOT)))
            elif path.read_text(encoding='utf-8') != content:
                drifted.append(path.relative_to(REPO_ROOT))
        if drifted:
            print('ДИЗАЙН-ДРЕЙФ: артефакты не совпадают с tokens.json:')
            for item in drifted:
                print('  - {item}'.format(item=item))
            print('Перегенерируйте: python scripts/build_design_tokens.py и закоммитьте.')
            return 1
        print('OK: tokens.css, design-tokens.js, design_colors.py, '
              'sw-bypass-list.js синхронны с источниками.')
        return 0

    for path, content in rendered.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        print('written: {path}'.format(path=path.relative_to(REPO_ROOT)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
