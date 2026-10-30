
import re

from django.urls import reverse

from storage.domain.services import create_folder
from storage.tests.test_t8_ultimate import T8UltimateTestBase


class T9FolderHtmlTests(T8UltimateTestBase):
    def test_every_folder_row_has_unified_context_attributes(self):
        root = create_folder(self.user_gip, self.project, 'РД АР', section=self.section)
        nested = create_folder(self.user_gip, self.project, 'Вложенная', parent=root)
        project_root = create_folder(self.user_gip, self.project, 'Новая папка')

        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
        )
        html = response.content.decode()

        for folder in (root, nested, project_root):
            self.assertRegex(
                html,
                r'<div[^>]*class="folder-row[^"]*"[^>]*data-folder-id="%d"' % folder.pk,
            )
            self.assertIn(f'data-folder-id="{folder.pk}"', html)
        self.assertEqual(html.count('data-folder-row'), 3)
        self.assertEqual(html.count('data-folder-menu'), 3)
