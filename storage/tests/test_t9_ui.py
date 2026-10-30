from django.urls import reverse

from storage.domain.models import Folder
from storage.domain.services import create_folder
from storage.tests.test_t8_ultimate import T8UltimateTestBase


class T9UiTests(T8UltimateTestBase):
    def test_table_uses_bootstrap5_and_bulk_checkboxes(self):
        self.make_document(code='T9-1')
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
        )

        self.assertContains(response, 'table table-hover table-bordered')
        self.assertContains(response, 'id="documentsTable"')
        self.assertContains(response, 'name="document_ids"')
        self.assertContains(response, 'id="selectAllDocuments"')
        self.assertContains(response, 'js/vendor/bootstrap.bundle.min.js')
        self.assertContains(response, 'js/vendor/htmx.min.js')
        self.assertContains(response, 'js/vendor/Sortable.min.js')
        self.assertNotContains(response, 'col-resizer')
        self.assertNotContains(response, 'class="th-filter')

    def test_htmx_filter_request_returns_only_documents_panel(self):
        approved = self.make_document(code='T9-APPROVED')
        self.make_document(code='T9-DRAFT')
        type(approved).objects.filter(pk=approved.pk).update(status='approved')

        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'status': 'approved'},
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn('<html', content)
        self.assertIn('id="documentsPanel"', content)
        self.assertIn('T9-APPROVED', content)
        self.assertNotIn('T9-DRAFT', content)

    def test_filter_panel_search_uses_django_filter(self):
        self.make_document(code='SEARCH-ONE')
        self.make_document(code='OTHER-TWO')

        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'q': 'SEARCH'},
            HTTP_HX_REQUEST='true',
        )

        content = response.content.decode()
        self.assertIn('SEARCH-ONE', content)
        self.assertNotIn('OTHER-TWO', content)
        self.assertEqual(
            [row['code'] for row in response.context['documents']],
            ['SEARCH-ONE'],
        )

    def test_folder_create_form_is_loaded_for_nested_parent(self):
        root = create_folder(self.user_gip, self.project, 'Root', section=self.section)
        self.client.force_login(self.user_gip)

        response = self.client.get(
            reverse('storage:folder_create_form', args=[self.project.pk]),
            {'section': self.section.pk, 'parent': root.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="folderNameInput"')
        self.assertContains(response, f'value="{root.pk}"')
        self.assertContains(response, f'value="{self.section.pk}"')

    def test_nested_folder_is_created_from_modal_form(self):
        root = create_folder(self.user_gip, self.project, 'Root', section=self.section)
        self.client.force_login(self.user_gip)

        response = self.client.post(
            reverse('storage:folder_create', args=[self.project.pk]),
            {
                'name': 'Nested folder',
                'section': self.section.pk,
                'parent': root.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        child = Folder.objects.get(project=self.project, name='Nested folder')
        self.assertEqual(child.parent_id, root.pk)
        self.assertEqual(child.section_id, self.section.pk)

    def test_folder_rename_form_contains_current_name(self):
        folder = create_folder(self.user_gip, self.project, 'Old name', section=self.section)
        self.client.force_login(self.user_gip)

        response = self.client.get(
            reverse('storage:folder_rename_form', args=[folder.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="folderRenameInput"')
        self.assertContains(response, 'value="Old name"')
        self.assertContains(
            response,
            reverse('storage:folder_rename', args=[folder.pk]),
        )
