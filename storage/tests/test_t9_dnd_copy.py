
from pathlib import Path

from django.urls import reverse

from storage.domain.models import Document
from storage.domain.services import create_folder
from storage.tests.test_t8_ultimate import T8UltimateTestBase


class T9DndCopyFolderTests(T8UltimateTestBase):
    def test_rows_expose_numeric_id_and_context_menu_actions(self):
        folder = create_folder(self.user_gip, self.project, 'РД', section=self.section)
        document = self.make_document(code='DND-BUG-1', folder=folder)
        self.client.force_login(self.user_gip)

        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
        )

        self.assertContains(response, f'data-document-id="{document.pk}"')
        self.assertContains(response, 'data-folder-menu')
        self.assertContains(response, 'Создать подпапку')
        self.assertContains(response, 'Переименовать')
        self.assertContains(response, 'Удалить')
        self.assertContains(response, reverse('storage:document_copy', args=[0]))
        self.assertContains(response, reverse('storage:folder_move', args=[0]))

    def test_copy_endpoint_duplicates_document_versions_and_files(self):
        folder = create_folder(self.user_gip, self.project, 'РД', section=self.section)
        document = self.make_document(code='COPY-SRC', folder=folder)
        source_version = document.current_version
        self.client.force_login(self.user_gip)

        response = self.client.post(
            reverse('storage:document_copy', args=[document.pk]),
            {'folder_id': folder.pk},
        )

        self.assertEqual(response.status_code, 200)
        copied = Document.objects.get(pk=response.json()['document_id'])
        self.assertNotEqual(copied.pk, document.pk)
        self.assertIn('копия', copied.code)
        self.assertEqual(copied.folder_id, folder.pk)
        self.assertEqual(copied.versions.count(), 1)
        copied_version = copied.current_version
        self.assertIsNotNone(copied_version)
        self.assertNotEqual(copied_version.file.name, source_version.file.name)
        self.assertEqual(
            Path(copied_version.file.path).read_bytes(),
            Path(source_version.file.path).read_bytes(),
        )

    def test_copy_endpoint_generates_unique_codes(self):
        document = self.make_document(code='COPY-UNIQUE')
        self.client.force_login(self.user_gip)
        url = reverse('storage:document_copy', args=[document.pk])

        first = self.client.post(url, {'section_id': self.section.pk})
        second = self.client.post(url, {'section_id': self.section.pk})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(first.json()['code'], second.json()['code'])

    def test_folder_can_be_nested_and_moved_to_section_root(self):
        root = create_folder(self.user_gip, self.project, 'Root', section=self.section)
        target = create_folder(self.user_gip, self.project, 'Target', section=self.section)
        child = create_folder(self.user_gip, self.project, 'Child', section=self.section)
        self.client.force_login(self.user_gip)

        response = self.client.post(
            reverse('storage:folder_move', args=[child.pk]),
            {'parent_id': target.pk},
        )

        self.assertEqual(response.status_code, 200)
        child.refresh_from_db()
        self.assertEqual(child.parent_id, target.pk)

        response = self.client.post(
            reverse('storage:folder_move', args=[child.pk]),
            {'section_id': self.section.pk},
        )

        self.assertEqual(response.status_code, 200)
        child.refresh_from_db()
        self.assertIsNone(child.parent_id)
        self.assertEqual(child.section_id, self.section.pk)

    def test_folder_cannot_move_into_itself_or_descendant(self):
        root = create_folder(self.user_gip, self.project, 'Root', section=self.section)
        child = create_folder(self.user_gip, self.project, 'Child', parent=root)
        self.client.force_login(self.user_gip)

        self_response = self.client.post(
            reverse('storage:folder_move', args=[root.pk]),
            {'parent_id': root.pk},
        )
        descendant_response = self.client.post(
            reverse('storage:folder_move', args=[root.pk]),
            {'parent_id': child.pk},
        )

        self.assertEqual(self_response.status_code, 400)
        self.assertEqual(descendant_response.status_code, 400)
        root.refresh_from_db()
        self.assertIsNone(root.parent_id)

    def test_folder_cannot_move_to_another_section(self):
        folder = create_folder(self.user_gip, self.project, 'РД', section=self.section)
        other = create_folder(
            self.user_gip, self.project, 'Другой', section=self.other_section
        )
        self.client.force_login(self.user_gip)

        response = self.client.post(
            reverse('storage:folder_move', args=[folder.pk]),
            {'parent_id': other.pk},
        )

        self.assertEqual(response.status_code, 400)
        folder.refresh_from_db()
        self.assertIsNone(folder.parent_id)

    def test_viewer_cannot_move_folders(self):
        folder = create_folder(self.user_gip, self.project, 'РД', section=self.section)
        target = create_folder(self.user_gip, self.project, 'Target', section=self.section)
        self.client.force_login(self.user_viewer)

        response = self.client.post(
            reverse('storage:folder_move', args=[folder.pk]),
            {'parent_id': target.pk},
        )

        self.assertEqual(response.status_code, 403)

