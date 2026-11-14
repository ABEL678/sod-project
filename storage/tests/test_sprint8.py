"""Спринт 8: тёмный графитовый хедер, фильтр-бар, вьювер.

- фильтры снова видимы: выпадающий бар + чипы «Отфильтровано» + сброс;
- колоночные воронки (.th-filter) убраны из JS;
- тёмный хедер: токены --c-bg-header = #1E293B, theme-color синхронизирован;
- полоса предпросмотра листов и наложение чертежей — разметка/JS.
"""
from django.test import TestCase
from django.urls import reverse

from storage.tests.test_sprint5 import Sprint5TestBase


class FilterBarTests(Sprint5TestBase):
    def _page(self, query=''):
        self.client.force_login(self.user_gip)
        url = reverse('storage:project_detail', args=[self.project.pk])
        return self.client.get(url + ('?' + query if query else ''))

    def test_filter_bar_visible_by_default(self):
        response = self._page('tab=documents')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="filter-bar')
        self.assertNotContains(response, 'class="th-filter')
        # поля фильтров присутствуют и видимы (не внутри d-none)
        self.assertContains(response, 'id="id_section"')
        self.assertContains(response, 'id="id_doc_type"')
        self.assertContains(response, 'id="quickFilter"')

    def test_filtered_state_shows_chips_and_reset(self):
        response = self._page('tab=documents&status=in_review')
        self.assertContains(response, 'filter-bar is-filtered')
        self.assertContains(response, 'Отфильтровано')
        self.assertContains(response, 'Статус:')
        self.assertContains(response, 'На проверке')
        self.assertContains(response, 'data-filter-chip-clear="status"')
        self.assertContains(response, 'data-filter-reset')

    def test_zone_chip_displayed(self):
        response = self._page('tab=documents&zone=PUBLISHED')
        self.assertContains(response, 'Состояние:')
        self.assertContains(response, 'Опубликовано')
        self.assertContains(response, 'data-filter-chip-clear="zone"')

    def test_section_chip_uses_section_name(self):
        response = self._page(
            f'tab=documents&section={self.section_ar.pk}'
        )
        self.assertContains(response, 'Раздел:')
        self.assertContains(response, 'АР')

    def test_no_chips_without_filters(self):
        response = self._page('tab=documents')
        self.assertNotContains(response, 'filter-chips')
        self.assertNotContains(response, 'data-filter-reset')


class ViewerCompareTests(Sprint5TestBase):
    def _detail(self, document):
        self.client.force_login(self.user_gip)
        return self.client.get(
            reverse('storage:document_detail', args=[document.pk]),
        )

    def test_document_detail_exposes_compare_menu_and_overlay(self):
        document = self.make_document(code='CMP-1')
        response = self._detail(document)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="compareMenuBtn"')
        self.assertContains(response, 'id="compareVersionSelect"')
        self.assertContains(response, 'data-compare-mode="overlay"')
        self.assertContains(response, 'data-compare-mode="side"')
        self.assertContains(response, 'data-compare-mode="slider"')
        self.assertContains(response, 'id="overlayStage"')
        self.assertContains(response, 'id="overlayControls"')
        self.assertContains(response, 'id="overlayOpacity"')

    def test_document_detail_exposes_pdf_urls_and_thumb_rail(self):
        document = self.make_document(code='CMP-2')
        response = self._detail(document)
        self.assertContains(response, 'data-pdf-urls=')
        self.assertContains(response, 'id="thumbRail"')
        self.assertContains(response, 'id="thumbsToggle"')

    def test_pdf_urls_cover_all_versions(self):
        import json as jsonlib
        import html as htmlmod

        from django.core.files.uploadedfile import SimpleUploadedFile
        from storage.domain.services import upload_document

        document = self.make_document(code='CMP-3')
        # upload_document находит документ по (проект, раздел, код)
        # и добавляет вторую версию.
        upload_document(
            user=self.user_gip,
            project=self.project,
            section=self.section_ar,
            code=document.code,
            name=document.name,
            doc_type='drawing',
            file=SimpleUploadedFile('CMP-3.pdf', b'pdf-v2'),
        )
        response = self._detail(document)
        viewer = next(
            chunk for chunk in response.content.decode().split('<div')
            if 'data-pdf-urls' in chunk
        )
        fragment = viewer.split('data-pdf-urls="')[1].split('"')[0]
        payload = jsonlib.loads(htmlmod.unescape(fragment))
        self.assertEqual(len(payload), 2)
        self.assertTrue(
            all(url.startswith('/download/') for url in payload.values())
        )

    def test_viewer_js_has_overlay_and_thumbs(self):
        with open('static/js/pdf_viewer.js', encoding='utf-8') as fh:
            js = fh.read()
        self.assertIn('overlayState', js)
        self.assertIn('mixBlendMode', js)
        self.assertIn('IntersectionObserver', js)
        self.assertIn('buildThumbRail', js)
        self.assertIn('prepareAlignedComparison', js)
        self.assertNotIn('sliderToggleBtn', js)


class FilterBarStaticTests(TestCase):
    def _read(self, path):
        with open(path, encoding='utf-8') as fh:
            return fh.read()

    def test_app_js_uses_filter_bar_and_dropped_column_funnels(self):
        js = self._read('static/js/app.js')
        self.assertIn('initFilterBar', js)
        self.assertNotIn('bindColumnFilter', js)
        # упоминается только в комментарии, не в рабочем коде
        self.assertNotIn("wrapper.className = 'th-filter", js)
        self.assertNotIn('header.appendChild(wrapper)', js)

    def test_dark_header_tokens(self):
        import json
        tokens = json.load(open('static/design-tokens/tokens.json'))
        self.assertEqual(tokens['color']['surface']['header'], '#1E293B')
        self.assertEqual(tokens['color']['text']['on-dark'], '#F8FAFC')
        self.assertEqual(tokens['color']['accent']['on-header'], '#9FB6DF')

    def test_theme_color_synced_dark(self):
        import json
        base = self._read('storage/adapters/templates/storage/base.html')
        self.assertIn('content="#1E293B"', base)
        self.assertNotIn('3A4558', base)
        manifest = json.load(open('static/manifest.webmanifest'))
        self.assertEqual(manifest['theme_color'], '#1E293B')
