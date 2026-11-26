"""Волна 2, аудит-5: кебаб-меню строк документа в таблице.

Пункт бэклога: «Досборка мобильной раскладки: фильтр-бар на 2 колонки,
кебабы в таблице». Фильтр-бар на две колонки уже реализован в
redesign.css (1e6e46c); здесь закрывается вторая половина — видимая
кнопка меню строки, потому что правый клик на тач-устройствах недоступен.
"""
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import reverse

from storage.tests.test_t8_ultimate import T8UltimateTestBase


class DocumentKebabMenuTests(T8UltimateTestBase):
    def test_each_document_row_has_kebab_button(self):
        self.make_document(code='KEBAB-1')
        self.make_document(code='KEBAB-2')
        self.client.force_login(self.user_gip)

        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
        )

        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count('data-doc-kebab'), 2)
        self.assertIn('data-doc-kebab', html)
        self.assertIn('aria-label="Действия с документом"', html)

    def test_kebab_is_present_without_workflow_actions(self):
        self.make_document(code='KEBAB-VIEWER')
        self.client.force_login(self.user_viewer)

        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
        )

        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-doc-kebab', html)
        # Для viewer workflow-кнопок нет, но кебаб остаётся.
        self.assertNotIn('data-role="workflow-actions"', html)

    def test_kebab_has_large_enough_mobile_target(self):
        css = Path('static/css/app.css').read_text(encoding='utf-8')
        self.assertIn('.doc-table .doc-kebab', css)
        self.assertIn('min-width: 36px', css)


class DocumentKebabStaticContractTests(SimpleTestCase):
    def _read(self, path):
        return Path(path).read_text(encoding='utf-8')

    def test_app_js_opens_row_menu_from_kebab(self):
        js = self._read('static/js/app.js')
        self.assertIn("closest('[data-doc-kebab]')", js)
        self.assertIn('openDocumentMenu(kebabRow', js)

    def test_app_js_rerender_keeps_kebab(self):
        js = self._read('static/js/app.js')
        self.assertIn('function renderWorkflowButtons', js)
        self.assertIn("'<span class=\"doc-actions\">'", js)
        self.assertIn('data-doc-kebab', js)

    def test_redesign_css_defines_kebab(self):
        css = self._read('static/css/redesign.css')
        self.assertIn('.doc-actions', css)
        self.assertIn('.doc-kebab', css)
        self.assertIn('.doc-kebab:focus-visible', css)
