
from django.urls import reverse

from storage.domain.services import create_folder
from storage.tests.test_t8_ultimate import T8UltimateTestBase


class T9DelegationTests(T8UltimateTestBase):
    def test_row_context_menu_and_move_modal_markup(self):
        folder = create_folder(self.user_gip, self.project, 'РД АР', section=self.section)
        document = self.make_document(code='CTX-1', folder=folder)
        self.client.force_login(self.user_gip)

        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
        )

        self.assertContains(response, 'id="docRowMenu"')
        for action in ('open', 'versions', 'share', 'move', 'delete'):
            self.assertContains(response, f'data-doc-action="{action}"')
        self.assertContains(response, 'id="documentMoveModal"')
        self.assertContains(response, 'id="documentMoveFolder"')
        self.assertContains(
            response,
            reverse('storage:document_detail', args=[document.pk]),
        )
        self.assertContains(
            response,
            reverse('storage:document_share_link', args=[document.pk]),
        )
        self.assertContains(
            response,
            reverse('storage:document_delete', args=[document.pk]),
        )
        self.assertContains(response, 'data-folder-row')
        self.assertContains(response, 'data-folder-menu')

    def test_document_delete_endpoint_moves_to_trash(self):
        document = self.make_document(code='CTX-DEL')
        self.client.force_login(self.user_gip)

        response = self.client.post(
            reverse('storage:document_delete', args=[document.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        document.refresh_from_db()
        self.assertTrue(document.is_deleted)

    def test_document_delete_endpoint_is_forbidden_for_viewer(self):
        document = self.make_document(code='CTX-DEL-2')
        self.client.force_login(self.user_viewer)

        response = self.client.post(
            reverse('storage:document_delete', args=[document.pk]),
        )

        self.assertEqual(response.status_code, 403)
        document.refresh_from_db()
        self.assertFalse(document.is_deleted)

    def test_document_delete_endpoint_is_post_only(self):
        document = self.make_document(code='CTX-DEL-3')
        self.client.force_login(self.user_gip)

        response = self.client.get(
            reverse('storage:document_delete', args=[document.pk]),
        )

        self.assertEqual(response.status_code, 405)
