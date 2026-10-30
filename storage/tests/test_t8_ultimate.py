import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from storage.domain.models import UserProfile
from storage.domain.services import (
    can_purge_trash,
    create_folder,
    delete_folder,
    get_folder_tree,
)
from storage.tests.test_t8_core import T8CoreTestBase


User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class T8UltimateTestBase(T8CoreTestBase):
    def setUp(self):
        super().setUp()
        self.user_admin = User.objects.create_user('company_admin', password='pass')
        UserProfile.objects.create(
            user=self.user_admin,
            role='admin',
            company=self.company,
        )


class FolderNavigationTests(T8UltimateTestBase):
    def test_folder_filter_shows_only_documents_of_that_folder(self):
        folder = create_folder(self.user_gip, self.project, 'Папка А', section=self.section)
        other_folder = create_folder(
            self.user_gip, self.project, 'Папка Б', section=self.section
        )
        document = self.make_document(code='FOLDER-A', folder=folder)
        other_document = self.make_document(code='FOLDER-B', folder=other_folder)
        self.make_document(code='SECTION-ONLY')

        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'folder': folder.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row['code'] for row in response.context['documents']],
            ['FOLDER-A'],
        )
        self.assertEqual(
            [row.record.code for row in response.context['table'].rows],
            ['FOLDER-A'],
        )
        self.assertEqual(response.context['selected_folder'], folder)
        self.assertContains(response, 'folder-selected')

    def test_section_filter_applies_automatically(self):
        self.make_document(code='AR-DOC')
        self.make_document(code='KR-DOC', section=self.other_section)

        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'section': self.other_section.pk},
        )

        self.assertEqual(
            [row['code'] for row in response.context['documents']],
            ['KR-DOC'],
        )

    def test_project_breadcrumbs_include_section_and_folder_chain(self):
        root = create_folder(self.user_gip, self.project, 'Корень', section=self.section)
        child = create_folder(self.user_gip, self.project, 'Вложенная', parent=root)
        self.make_document(code='CRUMB', folder=child)

        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'folder': child.pk},
        )

        labels = [crumb['label'] for crumb in response.context['breadcrumbs']]
        self.assertEqual(labels[0], self.project.name)
        self.assertIn('Корень', labels)
        self.assertIn('Вложенная', labels)
        self.assertTrue(response.context['breadcrumbs'][-1]['active'])
        self.assertContains(response, 'aria-label="Хлебные крошки"')

    def test_document_breadcrumbs_include_folder(self):
        folder = create_folder(self.user_gip, self.project, 'Документы', section=self.section)
        document = self.make_document(code='DOC-CRUMB', folder=folder)

        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:document_detail', args=[document.pk]),
        )

        labels = [crumb['label'] for crumb in response.context['breadcrumbs']]
        self.assertEqual(labels[0], self.project.name)
        self.assertIn('Документы', labels)
        self.assertEqual(labels[-1], 'DOC-CRUMB')
        self.assertContains(response, 'aria-label="Хлебные крошки"')

    def test_nested_folder_subtree_document_count(self):
        root = create_folder(self.user_gip, self.project, 'Корень', section=self.section)
        child = create_folder(self.user_gip, self.project, 'Вложенная', parent=root)
        self.make_document(code='ROOT-DOC', folder=root)
        self.make_document(code='CHILD-DOC-1', folder=child)
        self.make_document(code='CHILD-DOC-2', folder=child)

        tree = get_folder_tree(self.user_gip, self.project)
        root_node = next(folder for folder in tree if folder.pk == root.pk)
        self.assertEqual(root_node.document_count, 1)
        self.assertEqual(root_node.subtree_document_count, 3)


class TableHeaderUiTests(T8UltimateTestBase):
    def test_filters_are_built_into_panel(self):
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
        )

        self.assertContains(response, 'class="filter-panel"')
        self.assertContains(response, 'id="filtersForm"')
        self.assertContains(response, 'name="section"')
        self.assertContains(response, 'name="doc_type"')
        self.assertContains(response, 'name="status"')
        self.assertContains(response, 'name="q"')
        self.assertNotContains(response, 'class="th-filter')
        self.assertNotContains(response, 'class="col-drag"')

    def test_selection_counter_and_drop_zone_markup(self):
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
        )

        self.assertContains(response, 'bulk-bar-floating')
        self.assertContains(response, 'Выбрано <span id="bulkCount">0</span> файлов')
        self.assertContains(response, 'id="docDropOverlay"')
        self.assertContains(response, 'Отпустите файл, чтобы загрузить в')
        self.assertNotContains(response, 'app-main.drag-over')

    def test_sidebar_resizer_and_sortable_table_markup(self):
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
        )

        self.assertContains(response, 'id="sidebarResizer"')
        self.assertContains(response, 'id="documentsTable"')
        self.assertContains(response, 'data-column="code"')
        self.assertContains(response, 'js/vendor/Sortable.min.js')
        self.assertNotContains(response, 'class="col-drag"')

    def test_upload_returns_document_id_and_highlight_marks_row(self):
        folder = create_folder(self.user_gip, self.project, 'Drop', section=self.section)
        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:document_upload_ajax', args=[self.project.pk]),
            {
                'folder_id': folder.pk,
                'section_id': self.section.pk,
                'code': 'HIGHLIGHT-1',
                'name': 'Highlighted',
                'file': SimpleUploadedFile('highlight.txt', b'data'),
            },
        )

        self.assertEqual(response.status_code, 200)
        document_id = int(response['X-Document-Id'])

        page = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'folder': folder.pk, 'highlight': document_id},
        )
        self.assertEqual(page.context['highlight_id'], document_id)
        self.assertContains(page, 'doc-row-new')
        self.assertContains(page, f'data-document-id="{document_id}"')


class TrashProtectionTests(T8UltimateTestBase):
    def _create_deleted_folder_with_file(self):
        folder = create_folder(self.user_gip, self.project, 'Удаляемая', section=self.section)
        document = self.make_document(code='TRASH-FILE', folder=folder)
        version = document.current_version
        file_path = Path(version.file.path)
        self.assertTrue(file_path.exists())
        delete_folder(self.user_gip, folder)
        return folder, document, file_path

    def test_folder_delete_warning_uses_subtree_file_count(self):
        folder = create_folder(self.user_gip, self.project, 'С файлами', section=self.section)
        child = create_folder(self.user_gip, self.project, 'Вложенная', parent=folder)
        self.make_document(code='DEL-1', folder=folder)
        self.make_document(code='DEL-2', folder=child)

        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
        )
        self.assertContains(response, 'data-file-count="2"')
        self.assertContains(response, 'Они также будут перемещены в корзину')

    def test_can_purge_trash_only_for_company_admin(self):
        self.assertTrue(can_purge_trash(self.user_admin, self.project))
        self.assertFalse(can_purge_trash(self.user_gip, self.project))
        self.assertFalse(can_purge_trash(self.user_viewer, self.project))
        superuser = User.objects.create_superuser('root', password='pass')
        self.assertTrue(can_purge_trash(superuser, self.project))

    def test_trash_clear_button_visible_only_for_admin(self):
        folder, _document, _file_path = self._create_deleted_folder_with_file()

        self.client.force_login(self.user_gip)
        gip_page = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'tab': 'trash'},
        )
        self.assertEqual(gip_page.status_code, 200)
        self.assertNotContains(gip_page, 'id="trashClearButton"')

        self.client.force_login(self.user_admin)
        admin_page = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'tab': 'trash'},
        )
        self.assertContains(admin_page, 'Очистить корзину')
        self.assertContains(admin_page, f'id="trashClearButton"')

    def test_gip_cannot_empty_trash(self):
        folder, document, file_path = self._create_deleted_folder_with_file()

        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:trash_clear', args=[self.project.pk]),
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(type(document).objects.filter(pk=document.pk).exists())
        self.assertTrue(file_path.exists())

    def test_admin_empty_trash_deletes_files_and_database_rows(self):
        folder, document, file_path = self._create_deleted_folder_with_file()

        self.client.force_login(self.user_admin)
        response = self.client.post(
            reverse('storage:trash_clear', args=[self.project.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(type(document).objects.filter(pk=document.pk).exists())
        self.assertFalse(type(folder).objects.filter(pk=folder.pk).exists())
        self.assertFalse(file_path.exists())
