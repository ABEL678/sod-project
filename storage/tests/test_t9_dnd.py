from django.urls import reverse

from storage.domain.services import create_folder
from storage.tests.test_t8_ultimate import T8UltimateTestBase


class T9DragAndDropTests(T8UltimateTestBase):
    def test_table_rows_and_tree_are_drag_drop_ready(self):
        folder = create_folder(self.user_gip, self.project, 'РД', section=self.section)
        self.make_document(code='DND-1', folder=folder)

        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
        )

        self.assertContains(response, 'draggable="true"')
        self.assertContains(response, f'data-drop-folder="{folder.pk}"')
        self.assertContains(response, f'data-drop-section="{self.section.pk}"')
        self.assertContains(
            response,
            reverse('storage:document_move', args=[0]),
        )
        self.assertNotContains(response, 'value="move"')
        self.assertNotContains(response, 'Переместить</button>')

    def test_drop_on_folder_moves_document(self):
        folder = create_folder(self.user_gip, self.project, 'РД', section=self.section)
        document = self.make_document(code='DND-2')

        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {'folder_id': folder.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['folder_id'], folder.pk)
        document.refresh_from_db()
        self.assertEqual(document.folder_id, folder.pk)
        self.assertEqual(document.section_id, self.section.pk)

    def test_drop_on_section_moves_document_to_section_root(self):
        folder = create_folder(self.user_gip, self.project, 'РД', section=self.section)
        document = self.make_document(code='DND-3', folder=folder)

        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {'section_id': self.other_section.pk},
        )

        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.section_id, self.other_section.pk)
        self.assertIsNone(document.folder_id)

    def test_drop_is_forbidden_for_viewer(self):
        folder = create_folder(self.user_gip, self.project, 'РД', section=self.section)
        document = self.make_document(code='DND-4')

        self.client.force_login(self.user_viewer)
        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {'folder_id': folder.pk},
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn('error', response.json())
        document.refresh_from_db()
        self.assertIsNone(document.folder_id)

    def test_drop_on_missing_folder_returns_400(self):
        document = self.make_document(code='DND-5')
        self.client.force_login(self.user_gip)

        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {'folder_id': 999999},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['status'], 'error')

    def test_move_endpoint_is_post_only(self):
        document = self.make_document(code='DND-6')
        self.client.force_login(self.user_viewer)

        response = self.client.get(
            reverse('storage:document_move', args=[document.pk]),
        )

        self.assertEqual(response.status_code, 405)
