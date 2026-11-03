"""Спринт 5: надёжность и безопасность.

Покрывает фиксы генерального аудита 4.0:
- зоны ISO 19650 при переносе документа в другой раздел;
- матрица прав на редактирование ответа на замечание;
- валидация загружаемых файлов (расширение, размер);
- синхронизация статус-триплета при копировании;
- колоночные фильтры suitability_code/internal_status в сохранённом виде;
- гейт замечаний по публичной share-ссылке;
- парольная политика при создании пользователя.
"""
from unittest import mock

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from issues.models import Issue
from storage.adapters.local_storage import LocalFileStorage
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
    copy_document,
    get_project_documents_for_view,
    move_document_to_section,
    save_document_view,
    upload_document,
)

User = get_user_model()

PNG_BYTES = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
    b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


class Sprint5TestBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Компания С5')
        self.user_admin = User.objects.create_user('admin5', password='pass')
        self.user_gip = User.objects.create_user('gip5', password='pass')
        self.user_developer = User.objects.create_user('dev5', password='pass')
        self.user_viewer = User.objects.create_user('viewer5', password='pass')
        UserProfile.objects.create(user=self.user_admin, role='admin', company=self.company)
        UserProfile.objects.create(user=self.user_gip, role='gip', company=self.company)
        UserProfile.objects.create(user=self.user_developer, role='developer', company=self.company)
        UserProfile.objects.create(user=self.user_viewer, role='viewer', company=self.company)
        self.project = Project.objects.create(
            company=self.company,
            code='S5',
            name='Проект Спринта 5',
            status='active',
            gip=self.user_gip,
            created_by=self.user_gip,
        )
        self.section_ar = Section.objects.create(
            project=self.project, code='АР', name='АР', order=1,
        )
        self.section_kr = Section.objects.create(
            project=self.project, code='КР', name='КР', order=2,
        )
        self.published_folder = Folder.objects.create(
            project=self.project,
            section=self.section_ar,
            name='03. Опубликовано',
            zone='PUBLISHED',
            created_by=self.user_gip,
        )
        self.wip_folder = Folder.objects.create(
            project=self.project,
            section=self.section_kr,
            name='01. В работе',
            zone='WIP',
            created_by=self.user_gip,
        )

    def make_document(self, code='S5-1', folder=None, status='draft'):
        version = upload_document(
            user=self.user_gip,
            project=self.project,
            section=self.section_ar,
            code=code,
            name=f'Документ {code}',
            doc_type='drawing',
            file=SimpleUploadedFile(f'{code}.pdf', b'pdf-data'),
        )
        document = version.document
        document.status = status
        if folder is not None:
            document.folder = folder
        document.save(update_fields=['status', 'folder'])
        return document


class ZoneGuardOnSectionMoveTests(Sprint5TestBase):
    def test_cannot_move_published_document_to_another_section(self):
        document = self.make_document(code='PUB-1', folder=self.published_folder, status='approved')

        with self.assertRaises(PermissionDenied):
            move_document_to_section(
                self.user_gip, document, self.section_kr,
            )

        document.refresh_from_db()
        self.assertEqual(document.section_id, self.section_ar.pk)

    def test_wip_document_still_moves_between_sections(self):
        document = self.make_document(code='WIP-1', folder=self.wip_folder)

        moved = move_document_to_section(
            self.user_gip, document, self.section_kr,
        )

        self.assertEqual(moved.section_id, self.section_kr.pk)


class IssueAnswerPermissionTests(Sprint5TestBase):
    def setUp(self):
        super().setUp()
        self.document = self.make_document(code='ANS-1')
        self.issue = Issue.objects.create(
            document=self.document,
            author=self.user_gip,
            description='Не соответствует ГОСТ',
            status='NEW',
            assignee=self.user_developer,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user_developer,
            role_in_project='developer',
        )

    def test_viewer_cannot_edit_answer(self):
        from storage.domain.services import update_issue_answer

        with self.assertRaises(PermissionDenied):
            update_issue_answer(
                self.user_viewer, self.issue, 'подделка от наблюдателя',
            )

    def test_assignee_can_edit_answer(self):
        from storage.domain.services import update_issue_answer

        issue = update_issue_answer(
            self.user_developer, self.issue, 'Исправлено в v2',
        )
        self.assertEqual(issue.answer_text, 'Исправлено в v2')

    def test_gip_can_edit_answer(self):
        from storage.domain.services import update_issue_answer

        issue = update_issue_answer(self.user_gip, self.issue, 'Принято')
        self.assertEqual(issue.answer_text, 'Принято')


class UploadValidationTests(Sprint5TestBase):
    def test_executable_file_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            upload_document(
                user=self.user_gip,
                project=self.project,
                section=self.section_ar,
                code='EXE-1',
                name='Вирус',
                doc_type='drawing',
                file=SimpleUploadedFile('setup.exe', b'MZ...'),
            )
        self.assertIn('не поддерживается', str(ctx.exception))

    def test_empty_file_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            upload_document(
                user=self.user_gip,
                project=self.project,
                section=self.section_ar,
                code='EMPTY-1',
                name='Пустой',
                doc_type='drawing',
                file=SimpleUploadedFile('empty.pdf', b''),
            )
        self.assertIn('пустой', str(ctx.exception))

    def test_oversized_file_rejected(self):
        big = SimpleUploadedFile('big.pdf', b'a' * 1024)
        with mock.patch('storage.domain.services.MAX_UPLOAD_SIZE', 512):
            with self.assertRaises(ValueError) as ctx:
                upload_document(
                    user=self.user_gip,
                    project=self.project,
                    section=self.section_ar,
                    code='BIG-1',
                    name='Большой',
                    doc_type='drawing',
                    file=big,
                )
        self.assertIn('больше', str(ctx.exception))

    def test_pdf_upload_still_accepted(self):
        version = upload_document(
            user=self.user_gip,
            project=self.project,
            section=self.section_ar,
            code='OK-1',
            name='Чертёж',
            doc_type='drawing',
            file=SimpleUploadedFile('ok.pdf', b'%PDF-1.4 data'),
        )
        self.assertEqual(version.document.code, 'OK-1')


class CopyDocumentSyncTests(Sprint5TestBase):
    def test_copy_into_wip_folder_resets_status_triplet(self):
        document = self.make_document(
            code='CPY-1', folder=self.published_folder, status='approved',
        )
        document.suitability_code = 'A'
        document.save(update_fields=['suitability_code'])

        copied = copy_document(
            self.user_gip,
            document,
            folder=self.wip_folder,
            section=self.section_kr,
            file_storage=LocalFileStorage(),
        )

        self.assertEqual(copied.status, 'draft')
        self.assertEqual(copied.suitability_code, 'S0')
        self.assertEqual(copied.internal_status, 'draft')

    def test_copy_keeps_zone_when_target_is_published(self):
        document = self.make_document(
            code='CPY-2', folder=self.published_folder, status='approved',
        )
        document.suitability_code = 'A'
        document.save(update_fields=['suitability_code'])

        # Зона PUBLISHED доступна на запись только администратору — копию
        # в каноническую папку публикует админ (см. check_zone_permission).
        copied = copy_document(
            self.user_admin,
            document,
            folder=self.published_folder,
            section=self.section_ar,
            file_storage=LocalFileStorage(),
        )

        self.assertEqual(copied.status, 'approved')
        self.assertEqual(copied.suitability_code, 'A')


class SavedViewColumnFilterTests(Sprint5TestBase):
    def test_internal_status_filter_applies_to_saved_view(self):
        self.make_document(code='F1', folder=self.wip_folder)
        second = self.make_document(code='F2', folder=self.wip_folder)
        second.internal_status = 'review'
        second.save(update_fields=['internal_status'])

        view = save_document_view(
            self.user_gip,
            self.project,
            'На проверке',
            {
                'columns': ['code', 'internal_status'],
                'filters': {'internal_status': 'review'},
            },
        )
        rows = get_project_documents_for_view(
            self.user_gip, self.project, view.config,
        )

        self.assertEqual([row['code'] for row in rows], ['F2'])

    def test_suitability_filter_applies_to_saved_view(self):
        self.make_document(code='S0-1', folder=self.wip_folder)
        approved = self.make_document(
            code='A-1', folder=self.published_folder, status='approved',
        )
        approved.suitability_code = 'A'
        approved.save(update_fields=['suitability_code'])

        rows = get_project_documents_for_view(
            self.user_gip,
            self.project,
            {
                'columns': ['code', 'suitability'],
                'filters': {'suitability_code': 'A'},
            },
        )

        self.assertEqual([row['code'] for row in rows], ['A-1'])


class ShareLinkIssuesGateTests(Sprint5TestBase):
    def setUp(self):
        super().setUp()
        self.document = self.make_document(code='SHR-1')
        self.document.public_link_token = __import__('uuid').uuid4()
        self.document.public_link_expires_at = timezone.now() + timedelta(days=1)
        self.document.save(update_fields=[
            'public_link_token',
            'public_link_expires_at',
        ])
        Issue.objects.create(
            document=self.document,
            author=self.user_gip,
            description='Секретное замечание',
            status='NEW',
        )

    def test_public_view_hides_issues(self):
        response = self.client.get(
            reverse('storage:document_detail', args=[self.document.pk]),
            {'share': str(self.document.public_link_token)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['issues']), [])
        self.assertEqual(response.context['issues_json'], [])
        self.assertNotContains(response, 'Секретное замечание')

    def test_regular_view_still_shows_issues(self):
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:document_detail', args=[self.document.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['issues']), 1)
        self.assertContains(response, 'Секретное замечание')


class PasswordPolicyTests(Sprint5TestBase):
    def test_weak_password_rejected_on_member_creation(self):
        self.client.force_login(self.user_admin)
        response = self.client.post(
            reverse('storage:dashboard'),
            data={
                'action': 'add_member',
                'username': 'weakuser',
                'password': '123',
                'role': 'developer',
            },
        )

        self.assertFalse(
            User.objects.filter(username='weakuser').exists(),
            'Пароль «123» не должен проходить политику',
        )
        self.assertFalse(User.objects.filter(username='weakuser').exists())

    def test_strong_password_accepted(self):
        self.client.force_login(self.user_admin)
        self.client.post(
            reverse('storage:dashboard'),
            data={
                'action': 'add_member',
                'username': 'stronguser',
                'password': 'Tr0ub4dor&Xyzzy!',
                'role': 'developer',
            },
        )

        self.assertTrue(User.objects.filter(username='stronguser').exists())


class EmptyTrashCropCleanupTests(Sprint5TestBase):
    def test_crops_removed_on_trash_purge(self):
        from django.core.files.base import ContentFile
        from django.db import transaction as db_transaction

        document = self.make_document(code='CRP-1')
        issue = Issue.objects.create(
            document=document,
            author=self.user_gip,
            description='Замечание с маркапом',
            status='NEW',
        )
        issue.crop_image.save('crop.png', ContentFile(PNG_BYTES), save=True)
        crop_name = issue.crop_image.name
        document.is_deleted = True
        document.save(update_fields=['is_deleted'])

        from storage.domain.services import empty_trash
        from storage.adapters.local_storage import LocalFileStorage

        try:
            with self.captureOnCommitCallbacks(execute=True):
                result = empty_trash(
                    self.user_admin, self.project, LocalFileStorage(),
                )
        except PermissionDenied:
            self.fail('Администратор должен иметь право очистить корзину')

        self.assertEqual(result['documents'], 1)
        self.assertFalse(Issue.objects.filter(pk=issue.pk).exists())
        from django.core.files.storage import default_storage

        self.assertFalse(default_storage.exists(crop_name))


class Sprint6MarkupTests(Sprint5TestBase):
    """Спринт 6: UX-полировка — критичные элементы разметки на месте."""

    def test_project_page_has_visible_upload_button_and_status(self):
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
        )

        self.assertContains(response, 'id="uploadBtn"')
        self.assertContains(response, 'id="uploadStatus"')
        self.assertContains(response, 'id="panelSpinner"')
        self.assertContains(response, 'data-role="upload-btn"')

    def test_document_modals_rendered_once_per_page(self):
        # Спринт 6: общие блоки вынесены из цикла разделов — по одному экземпляру
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
        )
        html = response.content.decode()

        self.assertEqual(html.count('id="dndToast"'), 1)
        self.assertEqual(html.count('id="docRowMenu"'), 1)
        self.assertEqual(html.count('id="documentMoveModal"'), 1)

    def test_folder_rows_have_visible_kebab_menu(self):
        from storage.domain.services import create_folder

        create_folder(self.user_gip, self.project, 'С кебабом', section=self.section_ar)
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
        )
        html = response.content.decode()

        self.assertIn('data-folder-kebab', html)
        # 2 папки из фикстур (03. Опубликовано, 01. В работе) + «С кебабом»
        self.assertEqual(html.count('data-folder-kebab'), 3)

    def test_login_page_loads_design_tokens(self):
        response = self.client.get(reverse('login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'design-tokens/tokens.css')

    def test_base_page_loads_brand_fonts_and_skip_link(self):
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
        )

        self.assertContains(response, 'fonts/fonts.css')
        self.assertContains(response, 'skip-link')
        self.assertContains(response, 'id="main-content"')
