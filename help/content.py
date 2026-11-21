"""Загрузчик статей справки «Помощь» из docs/help/*.md.

Формат файла: YAML-шапка между строками `---`, затем Markdown-тело.
Обязательные поля шапки: slug, title. Остальные: section, order, keywords, updated.
Кэш в процессе; сигнатура кэша — (количество файлов, сумма mtime_ns).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from django.conf import settings
from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin

logger = logging.getLogger(__name__)

DEFAULT_SLUG = 'start'
DEFAULT_SECTION = 'Прочее'
DEFAULT_ORDER = 999

_FRONT_MATTER_RE = re.compile(r'\A---\s*\n(.*?)\n---\s*\n?', re.DOTALL)

# html=False: сырой HTML в статьях экранируется.
_md = (
    MarkdownIt('commonmark', options_update={'html': False})
    .use(anchors_plugin, min_level=1, max_level=6, permalink=False)
)


@dataclass(frozen=True)
class Article:
    slug: str
    title: str
    section: str
    order: int
    keywords: list[str]
    updated: str
    html: str
    plain_text: str
    headings: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class Section:
    name: str
    articles: list[Article]


_cache_signature: tuple[int, int] | None = None
_cache_articles: dict[str, Article] = {}


def _split_front_matter(text: str) -> tuple[dict, str]:
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        raise ValueError('нет YAML-шапки между строками ---')
    meta = yaml.safe_load(match.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError('шапка должна быть YAML-словарём')
    return meta, text[match.end():]


def _plain_text(tokens) -> str:
    parts: list[str] = []
    for token in tokens:
        if token.type in ('inline', 'code_block', 'fence') and token.content:
            parts.append(token.content.strip())
    return '\n'.join(part for part in parts if part)


def _headings(tokens) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for index, token in enumerate(tokens):
        if token.type != 'heading_open':
            continue
        anchor = token.attrGet('id') or ''
        inline = tokens[index + 1] if index + 1 < len(tokens) else None
        text = inline.content if inline is not None and inline.type == 'inline' else ''
        if anchor:
            result.append((anchor, text))
    return result


def _parse_file(path: Path) -> Article | None:
    try:
        meta, body = _split_front_matter(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        logger.warning('help: файл %s пропущен: %s', path.name, exc)
        return None

    slug = str(meta.get('slug') or '').strip()
    title = str(meta.get('title') or '').strip()
    if not slug or not title:
        logger.warning('help: файл %s пропущен: нет slug или title', path.name)
        return None

    try:
        order = int(meta.get('order', DEFAULT_ORDER))
    except (TypeError, ValueError):
        order = DEFAULT_ORDER

    keywords = meta.get('keywords') or []
    if not isinstance(keywords, list):
        keywords = [keywords]

    tokens = _md.parse(body)
    return Article(
        slug=slug,
        title=title,
        section=str(meta.get('section') or DEFAULT_SECTION).strip(),
        order=order,
        keywords=[str(item) for item in keywords],
        updated=str(meta.get('updated') or ''),
        html=_md.renderer.render(tokens, _md.options, {}),
        plain_text=_plain_text(tokens),
        headings=_headings(tokens),
    )


def _signature(files: list[Path]) -> tuple[int, int]:
    return len(files), sum(path.stat().st_mtime_ns for path in files)


def load_articles(force: bool = False) -> dict[str, Article]:
    """Возвращает статьи по slug. Ошибки файлов не валят страницу."""
    global _cache_signature, _cache_articles

    directory = Path(settings.HELP_CONTENT_DIR)
    files = sorted(directory.glob('*.md')) if directory.is_dir() else []
    if not files:
        logger.warning('help: в каталоге %s нет статей', directory)
        _cache_signature, _cache_articles = None, {}
        return {}

    signature = _signature(files)
    if not force and signature == _cache_signature:
        return _cache_articles

    # Дубликат slug: выигрывает файл с большим mtime (при равенстве — по имени).
    candidates: dict[str, tuple[int, Path, Article]] = {}
    for path in files:
        article = _parse_file(path)
        if article is None:
            continue
        mtime = path.stat().st_mtime_ns
        current = candidates.get(article.slug)
        if current is None or mtime > current[0]:
            if current is not None:
                logger.warning('help: slug %r в %s перекрыт файлом %s', article.slug, current[1].name, path.name)
            candidates[article.slug] = (mtime, path, article)
        else:
            logger.warning('help: дубликат slug %r в %s пропущен', article.slug, path.name)

    articles = {slug: item[2] for slug, item in candidates.items()}
    _cache_signature, _cache_articles = signature, articles
    return articles


def get_article(slug: str) -> Article | None:
    return load_articles().get(slug)


def toc_tree(articles: dict[str, Article]) -> list[Section]:
    """Группирует статьи по разделу; разделы и статьи — по order."""
    grouped: dict[str, list[Article]] = {}
    for article in articles.values():
        grouped.setdefault(article.section, []).append(article)

    sections = [
        Section(name=name, articles=sorted(items, key=lambda a: (a.order, a.slug)))
        for name, items in grouped.items()
    ]
    sections.sort(key=lambda s: (min(a.order for a in s.articles), s.name))
    return sections
