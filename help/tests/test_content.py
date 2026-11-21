import shutil
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from help import content


ARTICLE = """---
slug: {slug}
title: {title}
section: {section}
order: {order}
keywords: [пин, статус]
updated: 2026-11-20
---
# {title}

Текст статьи.

## Подраздел
Ещё текст.
"""


class HelpContentTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        content._cache_signature = None
        content._cache_articles = {}
        self.override = override_settings(HELP_CONTENT_DIR=self.tmp)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)
        content._cache_signature = None
        content._cache_articles = {}

    def write(self, name, text):
        path = self.tmp / name
        path.write_text(text, encoding='utf-8')
        return path

    def test_parses_front_matter_and_body(self):
        self.write('a.md', ARTICLE.format(slug='alpha', title='Альфа', section='Раздел', order=5))
        article = content.get_article('alpha')
        self.assertIsNotNone(article)
        self.assertEqual(article.title, 'Альфа')
        self.assertEqual(article.section, 'Раздел')
        self.assertEqual(article.order, 5)
        self.assertEqual(article.keywords, ['пин', 'статус'])
        self.assertEqual(article.updated, '2026-11-20')
        self.assertIn('Текст статьи.', article.plain_text)
        self.assertNotIn('<h1', article.plain_text)

    def test_headings_have_anchor_ids(self):
        self.write('a.md', ARTICLE.format(slug='alpha', title='Альфа', section='С', order=1))
        article = content.get_article('alpha')
        anchors = [anchor for anchor, _ in article.headings]
        self.assertIn('подраздел', anchors)
        self.assertIn('id="подраздел"', article.html)

    def test_raw_html_is_escaped(self):
        self.write('a.md', '---\nslug: x\ntitle: X\n---\n<script>alert(1)</script>\n')
        article = content.get_article('x')
        self.assertNotIn('<script>', article.html)
        self.assertIn('&lt;script&gt;', article.html)

    def test_file_without_slug_is_skipped(self):
        self.write('bad.md', '---\ntitle: Без slug\n---\nтекст\n')
        self.write('ok.md', ARTICLE.format(slug='ok', title='OK', section='С', order=1))
        with self.assertLogs('help.content', level='WARNING'):
            articles = content.load_articles(force=True)
        self.assertEqual(set(articles), {'ok'})

    def test_file_without_front_matter_is_skipped(self):
        self.write('plain.md', '# Просто текст\n')
        with self.assertLogs('help.content', level='WARNING'):
            self.assertEqual(content.load_articles(force=True), {})

    def test_empty_directory_returns_empty_dict_with_warning(self):
        with self.assertLogs('help.content', level='WARNING'):
            self.assertEqual(content.load_articles(force=True), {})

    def test_missing_directory_returns_empty_dict(self):
        with override_settings(HELP_CONTENT_DIR=self.tmp / 'missing'):
            with self.assertLogs('help.content', level='WARNING'):
                self.assertEqual(content.load_articles(force=True), {})

    def test_duplicate_slug_keeps_newest_file(self):
        old = self.write('old.md', ARTICLE.format(slug='dup', title='Старая', section='С', order=1))
        new = self.write('new.md', ARTICLE.format(slug='dup', title='Новая', section='С', order=1))
        import os
        os.utime(old, ns=(1_000_000_000, 1_000_000_000))
        os.utime(new, ns=(2_000_000_000, 2_000_000_000))
        with self.assertLogs('help.content', level='WARNING'):
            articles = content.load_articles(force=True)
        self.assertEqual(articles['dup'].title, 'Новая')

    def test_cache_invalidates_on_mtime_change(self):
        path = self.write('a.md', ARTICLE.format(slug='alpha', title='Первая', section='С', order=1))
        self.assertEqual(content.get_article('alpha').title, 'Первая')
        path.write_text(ARTICLE.format(slug='alpha', title='Вторая', section='С', order=1), encoding='utf-8')
        import os
        os.utime(path, ns=(3_000_000_000, 3_000_000_000))
        self.assertEqual(content.get_article('alpha').title, 'Вторая')

    def test_toc_groups_by_section_and_sorts_by_order(self):
        self.write('a.md', ARTICLE.format(slug='b', title='B', section='Раздел', order=20))
        self.write('b.md', ARTICLE.format(slug='a', title='A', section='Раздел', order=10))
        self.write('c.md', ARTICLE.format(slug='c', title='C', section='Другой', order=5))
        sections = content.toc_tree(content.load_articles(force=True))
        names = [section.name for section in sections]
        self.assertEqual(names, ['Другой', 'Раздел'])
        slugs = [a.slug for a in sections[1].articles]
        self.assertEqual(slugs, ['a', 'b'])
