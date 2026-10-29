import tempfile
import zipfile
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from storage.domain.models import (
    Company,
    Document,
    Folder,
    Project,
    ProjectMembership,
    Section,
    UserProfile,
)
from storage.domain.services import (
    create_folder,
    create_project,
    create_section,
    delete_document,
    delete_folder,
    generate_share_link,
    get_accessible_documents,
    get_flat_folders,
    get_folder_tree,
    get_trash,
    move_document,
    restore_document,
    restore_folder,
    update_project_member_role,
    upload_document,
)


User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class T8CoreTestBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Test Company')
        self.user_gip = User.objects.create_user('gip', password='pass')
        self.user_engineer = User.objects.create_user('engineer', password='pass')
        self.user_viewer = User.objects.create_user('viewer', password='pass')
        UserProfile.objects.create(user=self.user_gip, role='gip', company=self.company)
        UserProfile.objects.create(user=self.user_engineer, role='engineer', company=self.company)
        UserProfile.objects.create(user=self.user_viewer, role='viewer', company=self.company)
        self.project = Project.objects.create(
            company=self.company,
            code='T8-1',
            name='T8 Project',
            status='active',
            gip=self.user_gip,
            created_by=self.user_gip,
        )
        self.section = Section.objects.create(
            project=self.project,
            code='AR',
            name='Architecture',
            order=1,
        )
        self.other_section = Section.objects.create(
            project=self.project,
            code='KR',
            name='Structure',
            order=2,
        )

    def make_document(self, code='DOC-1', section=None, folder=None):
        version = upload_document(
            user=self.user_gip,
            project=self.project,
            section=section or self.section,
            code=code,
            name=f'Document {code}',
            doc_type='drawing',
            file=SimpleUploadedFile(f'{code}.txt', f'data {code}'.encode()),
        )
        document = version.document
        if folder is not None:
            document.folder = folder
            document.save(update_fields=['folder'])
        return document


class FolderServiceTests(T8CoreTestBase):
    def test_create_nested_folder_tree(self):
        root = create_folder(self.user_gip, self.project, 'Root', section=self.section)
        child = create_folder(self.user_gip, self.project, 'Child', parent=root)

        tree = get_folder_tree(self.user_gip, self.project)
        tree_root = next(folder for folder in tree if folder.pk == root.pk)
        self.assertIn(child.pk, [folder.pk for folder in tree_root.children_tree])

    def test_viewer_cannot_create_folder(self):
        with self.assertRaises(PermissionDenied):
            create_folder(self.user_viewer, self.project, 'Hidden', section=self.section)

    def test_move_document_to_folder(self):
        folder = create_folder(self.user_gip, self.project, 'Folder', section=self.section)
        document = self.make_document()
        move_document(self.user_gip, document, folder)
        document.refresh_from_db()
        self.assertEqual(document.folder_id, folder.pk)

    def test_move_document_rejects_foreign_section_folder(self):
        folder = create_folder(
            self.user_gip,
            self.project,
            'KR Folder',
            section=self.other_section,
        )
        document = self.make_document()
        with self.assertRaises(ValueError):
            move_document(self.user_gip, document, folder)

    def test_create_folder_rejects_foreign_project_section(self):
        other_project = Project.objects.create(
            company=self.company,
            code='T8-OTHER',
            name='Other project',
            status='active',
            gip=self.user_gip,
            created_by=self.user_gip,
        )
        foreign_section = Section.objects.create(
            project=other_project,
            code='X',
            name='Foreign',
            order=1,
        )
        parent = create_folder(self.user_gip, self.project, 'Project root')
        with self.assertRaises(ValueError):
            create_folder(
                self.user_gip,
                self.project,
                'Bad',
                section=foreign_section,
                parent=parent,
            )

    def test_folder_parent_cannot_be_self(self):
        folder = create_folder(
            self.user_gip,
            self.project,
            'Cycle',
            section=self.section,
        )
        folder.parent = folder
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                folder.save(update_fields=['parent'])

    def test_contractor_does_not_see_foreign_section_folders(self):
        contractor = User.objects.create_user('contractor', password='pass')
        profile = UserProfile.objects.create(
            user=contractor,
            role='contractor',
            company=self.company,
        )
        profile.allowed_sections.add(self.section)

        visible = create_folder(
            self.user_gip,
            self.project,
            'Visible',
            section=self.section,
        )
        hidden = create_folder(
            self.user_gip,
            self.project,
            'Hidden',
            section=self.other_section,
        )
        tree_ids = {folder.pk for folder in get_folder_tree(contractor, self.project)}
        flat_ids = {folder.pk for folder in get_flat_folders(contractor, self.project)}
        self.assertIn(visible.pk, tree_ids)
        self.assertNotIn(hidden.pk, tree_ids)
        self.assertNotIn(hidden.pk, flat_ids)

    def test_restore_folder_keeps_independently_deleted_document_deleted(self):
        folder = create_folder(self.user_gip, self.project, 'Mixed', section=self.section)
        active_document = self.make_document(code='ACTIVE', folder=folder)
        deleted_document = self.make_document(code='DELETED', folder=folder)
        delete_document(self.user_gip, deleted_document)

        delete_folder(self.user_gip, folder)
        restore_folder(self.user_gip, folder)

        active_document.refresh_from_db()
        deleted_document.refresh_from_db()
        self.assertFalse(active_document.is_deleted)
        self.assertTrue(deleted_document.is_deleted)

    def test_share_link_is_stable_and_persisted(self):
        document = self.make_document()
        token = generate_share_link(self.user_gip, document)
        document.refresh_from_db()
        self.assertEqual(str(document.public_link_token), token)
        self.assertEqual(generate_share_link(self.user_viewer, document), token)

    def test_soft_delete_and_restore_document(self):
        document = self.make_document()
        delete_document(self.user_gip, document)
        self.assertNotIn(document, get_accessible_documents(self.user_gip))
        self.assertIn(document, get_trash(self.user_gip, self.project)['documents'])
        restore_document(self.user_gip, document)
        document.refresh_from_db()
        self.assertFalse(document.is_deleted)
        self.assertIsNone(document.deleted_at)

    def test_delete_folder_moves_documents_to_trash(self):
        folder = create_folder(self.user_gip, self.project, 'Folder', section=self.section)
        document = self.make_document(folder=folder)

        delete_folder(self.user_gip, folder)
        folder.refresh_from_db()
        document.refresh_from_db()
        self.assertTrue(folder.is_deleted)
        self.assertTrue(document.is_deleted)
        self.assertIn(folder, get_trash(self.user_gip, self.project)['folders'])

        restore_folder(self.user_gip, folder)
        folder.refresh_from_db()
        document.refresh_from_db()
        self.assertFalse(folder.is_deleted)
        self.assertFalse(document.is_deleted)

    def test_create_section_requires_manager(self):
        with self.assertRaises(PermissionDenied):
            create_section(self.project, 'OV', 'Ventilation', 3, user=self.user_viewer)
        section = create_section(self.project, 'OV', 'Ventilation', 3, user=self.user_gip)
        self.assertEqual(section.code, 'OV')

    def test_create_project_rejects_viewer_as_gip(self):
        with self.assertRaises(ValueError):
            create_project(
                company=self.company,
                code='BAD-GIP',
                name='Bad GIP',
                customer='',
                gip=self.user_viewer,
                created_by=self.user_gip,
            )

    def test_membership_cannot_promote_viewer_to_gip(self):
        membership = ProjectMembership.objects.create(
            project=self.project,
            user=self.user_viewer,
            role_in_project='viewer',
        )
        with self.assertRaises(ValueError):
            update_project_member_role(self.user_gip, membership, 'gip')


class T8CoreViewTests(T8CoreTestBase):
    def test_project_list_shows_create_project_for_gip(self):
        self.client.force_login(self.user_gip)
        response = self.client.get(reverse('storage:project_list'))
        self.assertContains(response, 'Создать проект')
        self.assertContains(response, 'createProjectModal')

    def test_upload_document_into_folder(self):
        folder = create_folder(self.user_gip, self.project, 'Uploads', section=self.section)
        version = upload_document(
            user=self.user_gip,
            project=self.project,
            section=self.section,
            code='UPLOAD-1',
            name='Uploaded',
            doc_type='drawing',
            file=SimpleUploadedFile('upload.txt', b'upload'),
            folder=folder,
        )
        version.document.refresh_from_db()
        self.assertEqual(version.document.folder_id, folder.pk)

    def test_upload_ajax_into_folder(self):
        folder = create_folder(self.user_gip, self.project, 'Drop target', section=self.section)
        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:document_upload_ajax', args=[self.project.pk]),
            {
                'folder_id': folder.pk,
                'section_id': self.section.pk,
                'code': 'DROP-1',
                'name': 'Dropped file',
                'file': SimpleUploadedFile('drop.txt', b'drop'),
            },
        )
        self.assertEqual(response.status_code, 200)
        document = Document.objects.get(project=self.project, code='DROP-1')
        self.assertEqual(document.folder_id, folder.pk)

    def test_upload_ajax_rejects_foreign_folder(self):
        other_project = Project.objects.create(
            company=self.company,
            code='T8-FOREIGN',
            name='Foreign',
            status='active',
            gip=self.user_gip,
            created_by=self.user_gip,
        )
        foreign_folder = create_folder(
            self.user_gip,
            other_project,
            'Foreign folder',
        )
        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:document_upload_ajax', args=[self.project.pk]),
            {
                'folder_id': foreign_folder.pk,
                'section_id': self.section.pk,
                'code': 'DROP-2',
                'name': 'Dropped file',
                'file': SimpleUploadedFile('drop.txt', b'drop'),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Document.objects.filter(code='DROP-2').exists())

    def test_project_create_view(self):
        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:project_create'),
            {'code': 'T8-2', 'name': 'New Project', 'customer': 'Customer'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Project.objects.filter(code='T8-2').exists())

    def test_project_detail_contains_folder_tree_and_bulk_ui(self):
        folder = create_folder(self.user_gip, self.project, 'Folder', section=self.section)
        self.make_document(folder=folder)
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk])
        )
        self.assertContains(response, f'data-folder-id="{folder.pk}"')
        self.assertContains(response, 'folder-menu-btn')
        self.assertContains(response, 'selectAllDocuments')
        self.assertContains(response, 'doc-share-btn')
        self.assertContains(response, 'id="bulkForm"')
        self.assertContains(response, 'data-role="bulk-bar"')
        self.assertContains(response, 'Создать папку')

    def test_folder_create_and_section_create_views(self):
        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:section_create', args=[self.project.pk]),
            {'code': 'OV', 'name': 'Ventilation', 'order': 3},
        )
        self.assertEqual(response.status_code, 302)
        section = Section.objects.get(project=self.project, code='OV')

        response = self.client.post(
            reverse('storage:folder_create', args=[self.project.pk]),
            {'name': 'New Folder', 'section': section.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Folder.objects.filter(project=self.project, name='New Folder').exists()
        )

    def test_public_share_link_allows_anonymous_read(self):
        document = self.make_document()
        token = generate_share_link(self.user_gip, document)
        response = self.client.get(
            f"{reverse('storage:document_detail', args=[document.pk])}?share={token}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Публичный просмотр')
        self.assertNotContains(response, 'Добавить')

    def test_public_share_link_rejects_bad_token(self):
        document = self.make_document()
        response = self.client.get(
            f"{reverse('storage:document_detail', args=[document.pk])}?share=bad-token"
        )
        self.assertEqual(response.status_code, 404)

    def test_share_link_view_returns_url(self):
        document = self.make_document()
        self.client.force_login(self.user_viewer)
        response = self.client.get(
            reverse('storage:document_share_link', args=[document.pk])
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('token', payload)
        self.assertIn(str(document.pk), payload['url'])

    def test_bulk_move_and_delete_view(self):
        folder = create_folder(self.user_gip, self.project, 'Folder', section=self.section)
        document = self.make_document()
        self.client.force_login(self.user_gip)

        response = self.client.post(
            reverse('storage:document_bulk_action', args=[self.project.pk]),
            {
                'action': 'move',
                'folder': folder.pk,
                'document_ids': [document.pk],
            },
        )
        self.assertEqual(response.status_code, 302)
        document.refresh_from_db()
        self.assertEqual(document.folder_id, folder.pk)

        response = self.client.post(
            reverse('storage:document_bulk_action', args=[self.project.pk]),
            {
                'action': 'delete',
                'document_ids': [document.pk],
            },
        )
        self.assertEqual(response.status_code, 302)
        document.refresh_from_db()
        self.assertTrue(document.is_deleted)

    def test_bulk_download_returns_zip(self):
        document = self.make_document()
        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:document_bulk_action', args=[self.project.pk]),
            {'action': 'download', 'document_ids': [document.pk]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        archive = zipfile.ZipFile(BytesIO(response.content))
        self.assertEqual(len(archive.namelist()), 1)

    def test_trash_restore_view(self):
        document = self.make_document()
        delete_document(self.user_gip, document)
        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:trash_restore', args=[self.project.pk]),
            {'item_type': 'document', 'item_id': document.pk},
        )
        self.assertEqual(response.status_code, 302)
        document.refresh_from_db()
        self.assertFalse(document.is_deleted)

    def test_folder_download_returns_zip(self):
        folder = create_folder(self.user_gip, self.project, 'Folder', section=self.section)
        self.make_document(folder=folder)
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:folder_download', args=[folder.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        archive = zipfile.ZipFile(BytesIO(response.content))
        self.assertEqual(len(archive.namelist()), 1)

    def test_folder_download_preserves_nested_paths(self):
        root = create_folder(self.user_gip, self.project, 'Root', section=self.section)
        subfolder = create_folder(self.user_gip, self.project, 'subfolder', parent=root)
        self.make_document(code='NESTED', folder=subfolder)
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:folder_download', args=[root.pk])
        )
        archive = zipfile.ZipFile(BytesIO(response.content))
        names = archive.namelist()
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].startswith('subfolder/'))
