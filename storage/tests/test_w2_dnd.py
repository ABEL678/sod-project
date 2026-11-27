"""Волна 2, задача W2-7: регрессионная защита drag-and-drop документов.

Пункт бэклога аудита-5 «E2E-тесты DnD» закрывается в объёме, допустимом
правилами волны 2: без Playwright/Selenium/любых браузерных драйверов и
без новых зависимостей. Это не полноценный браузерный E2E, а два слоя:

1. Серверная сторона DnD (``POST /documents/<id>/move/``,
   ``POST /folders/<id>/move/``) — обычные Django-тесты: отказы по
   зонам, правам, чужим проектам и удалённым/чужим документам. Базовые
   сценарии (перенос в папку/раздел, циклы папок, viewer) уже закреплены
   в ``test_t9_dnd.py`` и ``test_t9_dnd_copy.py`` — здесь только
   незакрытые ветки.
2. Клиентская сторона — контрактные проверки в стиле W2-4: статические
   строки в ``static/js/app.js`` и в шаблонах не должны развязываться с
   сервером (data-атрибуты, MIME-типы, URL-шаблоны, HTMX-обновление).

Ручные шаги, которые нельзя проверить без браузера, вынесены владельцу
в отчёте задачи (открыть реестр → перетащить документ в папку → проверить).
"""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.urls import reverse

from storage.domain.models import (
    Company,
    Document,
    Folder,
    Project,
    Section,
    UserProfile,
)
from storage.domain.services import _ensure_project_zones, create_folder
from storage.tests.test_t8_ultimate import T8UltimateTestBase


User = get_user_model()

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DndZoneEndpointTests(T8UltimateTestBase):
    """DnD меняет только размещение; этап жизненного цикла сохраняется.

    Решение владельца 27.11.2026: папка и этап жизненного цикла —
    независимые атрибуты; переход между этапами — только явным
    workflow-действием. Системные папки этапов не являются целью DnD.
    """

    def setUp(self):
        super().setUp()
        _ensure_project_zones(self.project, self.user_gip)
        self.wip_zone = Folder.objects.get(project=self.project, zone='WIP')
        self.shared_zone = Folder.objects.get(project=self.project, zone='SHARED')
        self.published_zone = Folder.objects.get(project=self.project, zone='PUBLISHED')
        self.archive_zone = Folder.objects.get(project=self.project, zone='ARCHIVE')
        self.target_folder = create_folder(
            self.user_gip, self.project, 'Целевая', section=self.section,
        )

    def _document_in_stage(self, code, folder, state):
        document = self.make_document(code=code)
        document.state = state
        document.folder = folder
        document._from_service = True
        document.save(update_fields=['state', 'folder'])
        return document

    def test_published_document_drag_keeps_stage(self):
        """DnD опубликованного документа меняет папку, но не этап."""
        document = self._document_in_stage(
            'DND-Z-1', self.published_zone, Document.STATE_APPROVED,
        )

        self.client.force_login(self.user_admin)
        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {'folder_id': self.target_folder.pk},
        )

        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.folder_id, self.target_folder.pk)
        self.assertEqual(document.status, 'approved')
        self.assertEqual(document.state, Document.STATE_APPROVED)

    def test_shared_document_drag_by_gip_keeps_stage(self):
        """ГИП переносит документ на согласовании; этап остаётся SHARED."""
        document = self._document_in_stage(
            'DND-Z-6', self.shared_zone, Document.STATE_SHARED,
        )

        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {'folder_id': self.target_folder.pk},
        )

        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.folder_id, self.target_folder.pk)
        self.assertEqual(document.status, 'in_review')
        self.assertEqual(document.state, Document.STATE_SHARED)

    def test_archived_document_drag_by_admin_keeps_stage(self):
        """Админ может переставить архивный документ; этап остаётся архивным."""
        document = self._document_in_stage(
            'DND-Z-2', self.archive_zone, Document.STATE_ARCHIVED,
        )

        self.client.force_login(self.user_admin)
        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {'folder_id': self.target_folder.pk},
        )

        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.folder_id, self.target_folder.pk)
        self.assertEqual(document.status, 'archived')
        self.assertEqual(document.state, Document.STATE_ARCHIVED)

    def test_published_document_section_drag_keeps_stage(self):
        """DnD в другой раздел меняет только размещение, не этап."""
        document = self._document_in_stage(
            'DND-Z-3', self.published_zone, Document.STATE_APPROVED,
        )

        self.client.force_login(self.user_admin)
        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {
                'section_id': self.other_section.pk,
                'allow_section_change': '1',
            },
        )

        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.section_id, self.other_section.pk)
        self.assertIsNone(document.folder_id)
        self.assertEqual(document.status, 'approved')
        self.assertEqual(document.state, Document.STATE_APPROVED)

    def test_shared_document_reset_to_root_keeps_stage(self):
        """Сброс в корень (folder=None) меняет размещение, но не этап."""
        document = self._document_in_stage(
            'DND-Z-7', self.shared_zone, Document.STATE_SHARED,
        )

        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {},
        )

        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.assertIsNone(document.folder_id)
        self.assertEqual(document.section_id, self.section.pk)
        self.assertEqual(document.status, 'in_review')
        self.assertEqual(document.state, Document.STATE_SHARED)

    def test_developer_cannot_drop_into_system_stage_folder(self):
        """Системная папка этапа — не цель DnD: этап меняется workflow."""
        profile = UserProfile.objects.get(user=self.user_developer)
        profile.allowed_sections.add(self.section)
        document = self.make_document(code='DND-Z-4', folder=self.wip_zone)

        self.client.force_login(self.user_developer)
        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {'folder_id': self.shared_zone.pk},
        )

        self.assertEqual(response.status_code, 403)
        document.refresh_from_db()
        self.assertEqual(document.folder_id, self.wip_zone.pk)
        self.assertEqual(document.status, 'draft')

    def test_gip_cannot_drop_into_system_stage_folder(self):
        """Публикация — workflow-действие, а не DnD в системную папку."""
        document = self.make_document(code='DND-Z-5', folder=self.wip_zone)

        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {'folder_id': self.published_zone.pk},
        )

        self.assertEqual(response.status_code, 403)
        document.refresh_from_db()
        self.assertEqual(document.folder_id, self.wip_zone.pk)
        self.assertEqual(document.status, 'draft')


class DndPermissionEndpointTests(T8UltimateTestBase):
    """Права и изоляция проектов/компаний на эндпоинте перемещения."""

    def test_developer_can_drop_within_allowed_section(self):
        profile = UserProfile.objects.get(user=self.user_developer)
        profile.allowed_sections.add(self.section)
        target = create_folder(
            self.user_gip, self.project, 'Разрешённая', section=self.section,
        )
        document = self.make_document(code='DND-P-1')

        self.client.force_login(self.user_developer)
        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {'folder_id': target.pk},
        )

        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.folder_id, target.pk)
        self.assertEqual(document.state, Document.STATE_WIP_DRAFT)
        self.assertEqual(document.status, 'draft')

    def test_developer_cannot_drop_into_foreign_section_even_with_flag(self):
        profile = UserProfile.objects.get(user=self.user_developer)
        profile.allowed_sections.add(self.section)
        foreign_folder = create_folder(
            self.user_gip, self.project, 'Чужой раздел',
            section=self.other_section,
        )
        document = self.make_document(code='DND-P-2')

        self.client.force_login(self.user_developer)
        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {
                'folder_id': foreign_folder.pk,
                'allow_section_change': '1',
            },
        )

        self.assertEqual(response.status_code, 403)
        document.refresh_from_db()
        self.assertIsNone(document.folder_id)
        self.assertEqual(document.section_id, self.section.pk)

    def test_contractor_cannot_move_into_section_outside_allowed(self):
        """Смежник пишет только в allowed_sections — DnD не исключение."""
        contractor = User.objects.create_user('contractor', password='pass')
        profile = UserProfile.objects.update_or_create(
            user=contractor,
            defaults={'role': 'contractor', 'company': self.company},
        )[0]
        profile.allowed_sections.add(self.section)
        foreign_folder = create_folder(
            self.user_gip, self.project, 'Чужой раздел',
            section=self.other_section,
        )
        document = self.make_document(code='DND-P-3')

        self.client.force_login(contractor)
        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {
                'folder_id': foreign_folder.pk,
                'allow_section_change': '1',
            },
        )

        self.assertEqual(response.status_code, 403)
        document.refresh_from_db()
        self.assertIsNone(document.folder_id)
        self.assertEqual(document.section_id, self.section.pk)

    def test_drop_on_foreign_project_folder_is_rejected(self):
        other_project = Project.objects.create(
            company=self.company,
            code='T8-FOREIGN',
            name='Foreign project',
            status='active',
            created_by=self.user_gip,
        )
        other_section = Section.objects.create(
            project=other_project, code='AR', name='Foreign', order=1,
        )
        foreign_folder = Folder.objects.create(
            project=other_project,
            section=other_section,
            name='Foreign folder',
            zone='WIP',
            created_by=self.user_gip,
        )
        document = self.make_document(code='DND-P-4')

        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {'folder_id': foreign_folder.pk},
        )

        self.assertEqual(response.status_code, 400)
        document.refresh_from_db()
        self.assertIsNone(document.folder_id)
        self.assertEqual(document.section_id, self.section.pk)

    def test_trashed_document_is_not_a_drop_source(self):
        folder = create_folder(
            self.user_gip, self.project, 'Целевая', section=self.section,
        )
        document = self.make_document(code='DND-P-5')
        Document.objects.filter(pk=document.pk).update(is_deleted=True)

        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {'folder_id': folder.pk},
        )

        self.assertEqual(response.status_code, 404)
        document.refresh_from_db()
        self.assertIsNone(document.folder_id)

    def test_document_of_another_company_is_not_a_drop_source(self):
        other_company = Company.objects.create(name='Other Company')
        other_admin = User.objects.create_user('other_admin', password='pass')
        UserProfile.objects.update_or_create(
            user=other_admin,
            defaults={'role': 'admin', 'company': other_company},
        )
        other_project = Project.objects.create(
            company=other_company,
            code='OTHER-1',
            name='Other project',
            status='active',
            created_by=other_admin,
        )
        other_section = Section.objects.create(
            project=other_project, code='AR', name='Architecture', order=1,
        )
        foreign_document = Document.objects.create(
            project=other_project,
            section=other_section,
            code='OTHER-DOC',
            name='Other document',
            doc_type='drawing',
            created_by=other_admin,
        )
        folder = create_folder(
            self.user_gip, self.project, 'Целевая', section=self.section,
        )

        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:document_move', args=[foreign_document.pk]),
            {'folder_id': folder.pk},
        )

        self.assertEqual(response.status_code, 404)


class DndFolderEndpointTests(T8UltimateTestBase):
    """DnD папок: отказы, которые не покрыты test_t9_dnd_copy.py."""

    def test_folder_with_documents_cannot_move_to_project_root(self):
        folder = create_folder(
            self.user_gip, self.project, 'С документами', section=self.section,
        )
        self.make_document(code='DND-F-1', folder=folder)

        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:folder_move', args=[folder.pk]),
            {},
        )

        self.assertEqual(response.status_code, 400)
        folder.refresh_from_db()
        self.assertEqual(folder.section_id, self.section.pk)

    def test_folder_cannot_move_into_canonical_zone(self):
        _ensure_project_zones(self.project, self.user_gip)
        wip_zone = Folder.objects.get(project=self.project, zone='WIP')
        folder = create_folder(
            self.user_gip, self.project, 'Обычная', section=self.section,
        )

        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:folder_move', args=[folder.pk]),
            {'parent_id': wip_zone.pk},
        )

        self.assertEqual(response.status_code, 400)
        folder.refresh_from_db()
        self.assertIsNone(folder.parent_id)

    def test_folder_cannot_move_into_another_project_folder(self):
        other_project = Project.objects.create(
            company=self.company,
            code='T8-FOREIGN-2',
            name='Foreign project',
            status='active',
            created_by=self.user_gip,
        )
        other_section = Section.objects.create(
            project=other_project, code='AR', name='Foreign', order=1,
        )
        foreign_folder = Folder.objects.create(
            project=other_project,
            section=other_section,
            name='Foreign folder',
            zone='WIP',
            created_by=self.user_gip,
        )
        folder = create_folder(
            self.user_gip, self.project, 'Своя', section=self.section,
        )

        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('storage:folder_move', args=[folder.pk]),
            {'parent_id': foreign_folder.pk},
        )

        self.assertEqual(response.status_code, 400)
        folder.refresh_from_db()
        self.assertIsNone(folder.parent_id)

    def test_developer_cannot_move_folders(self):
        folder = create_folder(
            self.user_gip, self.project, 'Своя', section=self.section,
        )
        target = create_folder(
            self.user_gip, self.project, 'Целевая', section=self.section,
        )

        self.client.force_login(self.user_developer)
        response = self.client.post(
            reverse('storage:folder_move', args=[folder.pk]),
            {'parent_id': target.pk},
        )

        self.assertEqual(response.status_code, 403)
        folder.refresh_from_db()
        self.assertIsNone(folder.parent_id)


class DndMarkupContractTests(T8UltimateTestBase):
    """Серверный рендер отдаёт все крючки, на которые смотрит app.js."""

    def test_page_exposes_drag_sources_drop_targets_and_url_templates(self):
        folder = create_folder(
            self.user_gip, self.project, 'РД DnD', section=self.section,
        )
        document = self.make_document(code='DND-M-1', folder=folder)

        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
        )

        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        # Строка-источник dragstart.
        self.assertIn(f'data-document-id="{document.pk}"', html)
        self.assertIn('draggable="true"', html)
        self.assertIn('data-zone="WIP"', html)
        self.assertIn(f'data-section-id="{self.section.pk}"', html)
        # Цели drop: папка и раздел.
        self.assertIn(f'data-drop-folder="{folder.pk}"', html)
        self.assertIn(f'data-drop-name="{folder.name}"', html)
        self.assertIn(f'data-drop-section="{self.section.pk}"', html)
        # URL-шаблоны, которые app.js подставляет вместо /0/.
        self.assertIn(
            f'data-move-url-template="{reverse("storage:document_move", args=[0])}"',
            html,
        )
        self.assertIn(
            'data-folder-move-url-template="'
            f'{reverse("storage:folder_move", args=[0])}"',
            html,
        )
        self.assertIn('id="documentsTable"', html)


class DndClientContractTests(T8UltimateTestBase):
    """Контракт клиентской части DnD (W2-4 style, без браузера)."""

    def _read(self, path):
        return (PROJECT_ROOT / path).read_text(encoding='utf-8')

    def _js_block(self, js, start_marker, end_marker):
        """Возвращает участок app.js между двумя маркерами."""
        start = js.find(start_marker)
        self.assertNotEqual(start, -1, f'не найден маркер: {start_marker}')
        end = js.find(end_marker, start)
        self.assertNotEqual(end, -1, f'не найден маркер: {end_marker}')
        return js[start:end]

    def test_app_js_has_delegated_dnd_handlers(self):
        js = self._read('static/js/app.js')
        self.assertIn('function initDocumentDragAndDrop()', js)
        self.assertIn("document.addEventListener('dragstart'", js)
        self.assertIn("document.addEventListener('dragend'", js)
        self.assertIn("document.addEventListener('dragover'", js)
        self.assertIn("document.addEventListener('drop'", js)
        self.assertIn('initDocumentDragAndDrop();', js)
        # Делегирование: строки переживают HTMX-замену панели.
        self.assertIn(
            "'#documentsTable tbody tr[data-document-id]'",
            js,
        )

    def test_app_js_payload_matches_server_field_names(self):
        js = self._read('static/js/app.js')
        self.assertIn(
            "'application/x-sod-document-id'",
            js,
        )
        self.assertIn("'application/x-sod-folder-id'", js)
        self.assertIn('body.append(\'folder_id\'', js)
        self.assertIn('body.append(\'section_id\'', js)
        self.assertIn('folderBody.append(\'parent_id\'', js)
        self.assertIn("body.append('allow_section_change', '1')", js)
        self.assertIn("headers: {'X-CSRFToken': csrfToken()}", js)

    def test_app_js_uses_panel_url_templates(self):
        js = self._read('static/js/app.js')
        self.assertIn('root.dataset.moveUrlTemplate', js)
        self.assertIn('root.dataset.folderMoveUrlTemplate', js)
        self.assertIn('apiUrl(root.dataset.folderMoveUrlTemplate', js)

    def test_app_js_guards_archive_and_confirms_section_change(self):
        js = self._read('static/js/app.js')
        drop_handler = self._js_block(
            js,
            "document.addEventListener('drop', function (event) {",
            '/* ---------- Tables helpers ---------- */',
        )
        self.assertIn('function isArchiveZone(target)', js)
        self.assertIn(
            'isDocumentDrag(event) && isArchiveZone(target)', drop_handler,
        )
        self.assertIn(
            "showDndToast('Нельзя переместить файл в архив', true)",
            drop_handler,
        )
        # Оба подтверждения смены раздела живут в drop-обработчике и
        # действительно вызывают confirm, а не просто содержат текст.
        self.assertGreaterEqual(drop_handler.count('window.confirm('), 2)
        self.assertIn(
            'Переместить документ в папку другого раздела', drop_handler,
        )
        self.assertIn('Это изменит его марку.', drop_handler)
        self.assertIn("body.append('allow_section_change', '1')", drop_handler)
        self.assertIn('Сменить раздел', js)

    def test_app_js_refreshes_panel_and_tree_counts_after_drop(self):
        js = self._read('static/js/app.js')
        post_move = self._js_block(
            js,
            'function postMoveRequest(',
            'function initDocumentDragAndDrop(',
        )
        init_block = self._js_block(
            js,
            'function init() {',
            "if (document.readyState === 'loading') {",
        )
        self.assertIn('function refreshDocumentsPanel()', js)
        self.assertIn('function refreshTreeCounts(sourceDocument)', js)
        self.assertIn('function postMoveRequest(url, body, successMessage, options)', js)
        self.assertIn("method: 'POST'", post_move)
        self.assertIn('initHtmx();', init_block)

    def test_panel_and_tree_templates_keep_dnd_hooks(self):
        panel = self._read(
            'storage/adapters/templates/storage/_documents_panel.html'
        )
        tree = self._read(
            'storage/adapters/templates/storage/_folder_tree.html'
        )
        page = self._read(
            'storage/adapters/templates/storage/project_detail.html'
        )
        self.assertIn('data-move-url-template', panel)
        self.assertIn('data-folder-move-url-template', panel)
        self.assertIn('data-drop-folder="{{ folder.pk }}"', tree)
        self.assertIn('data-drop-name', tree)
        self.assertIn('draggable="true"', tree)
        self.assertIn('data-section-id', tree)
        self.assertIn('data-drop-section', page)
        self.assertIn('data-drop-name', page)
