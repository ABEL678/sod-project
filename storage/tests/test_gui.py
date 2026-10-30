from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from storage.domain.models import (
    Company,
    Document,
    DocumentView,
    Project,
    ProjectMembership,
    Section,
    UserProfile,
)
from storage.domain.services import (
    can_manage_project_participants,
    get_document_view,
    get_project_documents_for_view,
    get_project_membership,
    get_sections_with_counts,
    list_document_views,
    list_project_participants,
    normalize_document_view_config,
    save_document_view,
    update_project_member_role,
)


User = get_user_model()


class GuiTestBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Компания 1')
        self.other_company = Company.objects.create(name='Компания 2')

        self.user_admin = User.objects.create_user('admin', password='pass')
        self.user_gip = User.objects.create_user('gip', password='pass')
        self.user_engineer = User.objects.create_user('engineer', password='pass')
        self.user_viewer = User.objects.create_user('viewer', password='pass')
        self.user_contractor = User.objects.create_user('contractor', password='pass')
        self.user_foreign_gip = User.objects.create_user('foreign_gip', password='pass')

        UserProfile.objects.create(user=self.user_admin, role='admin', company=self.company)
        UserProfile.objects.create(user=self.user_gip, role='gip', company=self.company)
        UserProfile.objects.create(user=self.user_engineer, role='engineer', company=self.company)
        UserProfile.objects.create(user=self.user_viewer, role='viewer', company=self.company)
        UserProfile.objects.create(user=self.user_contractor, role='contractor', company=self.company)
        UserProfile.objects.create(
            user=self.user_foreign_gip,
            role='gip',
            company=self.other_company,
        )

        self.project = Project.objects.create(
            company=self.company,
            code='P-1',
            name='Проект',
            status='active',
            gip=self.user_gip,
            created_by=self.user_gip,
        )
        self.foreign_project = Project.objects.create(
            company=self.other_company,
            code='P-FOREIGN',
            name='Чужой проект',
            status='active',
            gip=self.user_foreign_gip,
            created_by=self.user_foreign_gip,
        )

        self.section_ar = Section.objects.create(
            project=self.project,
            code='АР',
            name='Архитектурные решения',
            order=1,
        )
        self.section_kr = Section.objects.create(
            project=self.project,
            code='КР',
            name='Конструктивные решения',
            order=2,
        )

        self.doc_ar_1 = Document.objects.create(
            project=self.project,
            section=self.section_ar,
            code='АР-01',
            name='План 1 этажа',
            doc_type='drawing',
            status='draft',
            created_by=self.user_gip,
        )
        self.doc_ar_2 = Document.objects.create(
            project=self.project,
            section=self.section_ar,
            code='АР-02',
            name='План 2 этажа',
            doc_type='drawing',
            status='approved',
            created_by=self.user_gip,
        )
        self.doc_kr_1 = Document.objects.create(
            project=self.project,
            section=self.section_kr,
            code='КР-01',
            name='Разрез',
            doc_type='drawing',
            status='in_review',
            created_by=self.user_gip,
        )

        self.membership_engineer = ProjectMembership.objects.create(
            project=self.project,
            user=self.user_engineer,
            role_in_project='engineer',
        )
        self.membership_viewer = ProjectMembership.objects.create(
            project=self.project,
            user=self.user_viewer,
            role_in_project='viewer',
        )

        self.user_contractor.userprofile.allowed_sections.add(self.section_ar)


class DocumentViewServiceTests(GuiTestBase):
    def test_save_and_list_personal_view(self):
        view = save_document_view(
            self.user_engineer,
            self.project,
            'Мой вид',
            {
                'columns': ['code', 'status'],
                'filters': {'status': 'draft'},
                'sort': ['-date'],
            },
        )

        self.assertFalse(view.is_shared)
        self.assertEqual(view.user, self.user_engineer)
        self.assertEqual(view.config['columns'], ['code', 'status'])
        self.assertEqual(view.config['filters'], {'status': 'draft'})
        self.assertEqual(view.config['sort'], ['-date'])

        views = list_document_views(self.user_engineer, self.project)
        self.assertIn(view, views)

    def test_personal_view_hidden_from_other_user(self):
        view = save_document_view(
            self.user_engineer,
            self.project,
            'Личный',
            {'columns': ['code']},
        )

        self.assertNotIn(view, list_document_views(self.user_viewer, self.project))
        self.assertIsNone(get_document_view(self.user_viewer, self.project, view.pk))

    def test_shared_view_visible_to_all_project_users(self):
        view = save_document_view(
            self.user_gip,
            self.project,
            'Общий',
            {'columns': ['code', 'name']},
            is_shared=True,
        )

        self.assertTrue(view.is_shared)
        self.assertIsNone(view.user)
        self.assertIn(view, list_document_views(self.user_engineer, self.project))
        self.assertEqual(
            get_document_view(self.user_engineer, self.project, view.pk),
            view,
        )

    def test_shared_view_requires_gip_or_admin(self):
        with self.assertRaises(PermissionDenied):
            save_document_view(
                self.user_engineer,
                self.project,
                'Общий',
                {'columns': ['code']},
                is_shared=True,
            )

        admin_view = save_document_view(
            self.user_admin,
            self.project,
            'Админский',
            {'columns': ['code']},
            is_shared=True,
        )
        self.assertTrue(admin_view.is_shared)

    def test_repeated_save_updates_existing_view(self):
        first = save_document_view(
            self.user_engineer,
            self.project,
            'Мой вид',
            {'columns': ['code']},
        )
        second = save_document_view(
            self.user_engineer,
            self.project,
            'Мой вид',
            {'columns': ['name', 'status']},
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            DocumentView.objects.filter(user=self.user_engineer, name='Мой вид').count(),
            1,
        )
        second.refresh_from_db()
        self.assertEqual(second.config['columns'], ['name', 'status'])

    def test_config_validation(self):
        invalid_configs = [
            {'columns': ['unknown']},
            {'columns': []},
            {'columns': ['code'], 'filters': {'unknown': 'x'}},
            {'columns': ['code'], 'filters': {'status': 'unknown'}},
            {'columns': ['code'], 'sort': ['unknown']},
            {'columns': ['code'], 'extra': True},
        ]
        for config in invalid_configs:
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    normalize_document_view_config(config)

        with self.assertRaises(ValueError):
            save_document_view(self.user_engineer, self.project, ' ', {'columns': ['code']})

    def test_widths_validated_in_config(self):
        config = normalize_document_view_config({
            'columns': ['code', 'name'],
            'widths': {'code': 120, 'name': '300'},
        })
        self.assertEqual(config['widths'], {'code': 120, 'name': 300})

        view = save_document_view(
            self.user_engineer,
            self.project,
            'С ширинами',
            {
                'columns': ['code', 'name'],
                'widths': {'code': 90, 'name': 480},
            },
        )
        self.assertEqual(view.config['widths'], {'code': 90, 'name': 480})

        invalid_widths = [
            {'code': 49},
            {'code': 801},
            {'unknown': 120},
            {'code': 'wide'},
            {'code': True},
            [],
        ]
        for widths in invalid_widths:
            with self.subTest(widths=widths):
                with self.assertRaises(ValueError):
                    normalize_document_view_config({
                        'columns': ['code'],
                        'widths': widths,
                    })

    def test_widths_infinity_raises(self):
        for value in (float('inf'), float('-inf'), 1e400):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_document_view_config({
                        'columns': ['code'],
                        'widths': {'code': value},
                    })

    def test_widths_float_raises(self):
        for value in (100.9, 100.5, float('nan')):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_document_view_config({
                        'columns': ['code'],
                        'widths': {'code': value},
                    })

        config = normalize_document_view_config({
            'columns': ['code'],
            'widths': {'code': 100.0},
        })
        self.assertEqual(config['widths'], {'code': 100})

    def test_foreign_project_forbidden(self):
        with self.assertRaises(PermissionDenied):
            save_document_view(
                self.user_foreign_gip,
                self.project,
                'Чужой',
                {'columns': ['code']},
            )

        with self.assertRaises(PermissionDenied):
            save_document_view(
                self.user_gip,
                self.foreign_project,
                'Чужой',
                {'columns': ['code']},
            )

    def test_sections_with_counts(self):
        sections = {section.code: section for section in get_sections_with_counts(self.user_gip, self.project)}

        self.assertEqual(sections['АР'].document_count, 2)
        self.assertEqual(sections['КР'].document_count, 1)

        contractor_sections = list(
            get_sections_with_counts(self.user_contractor, self.project)
        )
        self.assertEqual([section.code for section in contractor_sections], ['АР'])
        self.assertEqual(contractor_sections[0].document_count, 2)

    def test_project_documents_for_view_applies_filters(self):
        rows = get_project_documents_for_view(
            self.user_gip,
            self.project,
            {'columns': ['code', 'status'], 'filters': {'status': 'approved'}},
        )

        self.assertEqual([row['code'] for row in rows], ['АР-02'])
        self.assertEqual(rows[0]['status'], 'Утвержден')

    def test_can_manage_participants(self):
        self.assertTrue(can_manage_project_participants(self.user_admin, self.project))
        self.assertTrue(can_manage_project_participants(self.user_gip, self.project))
        self.assertFalse(can_manage_project_participants(self.user_engineer, self.project))
        self.assertFalse(can_manage_project_participants(self.user_viewer, self.project))
        self.assertFalse(can_manage_project_participants(self.user_foreign_gip, self.project))

    def test_update_member_role(self):
        update_project_member_role(
            self.user_gip,
            self.membership_engineer,
            'viewer',
        )
        self.membership_engineer.refresh_from_db()
        self.assertEqual(self.membership_engineer.role_in_project, 'viewer')

        with self.assertRaises(PermissionDenied):
            update_project_member_role(
                self.user_engineer,
                self.membership_viewer,
                'engineer',
            )

        with self.assertRaises(ValueError):
            update_project_member_role(
                self.user_gip,
                self.membership_viewer,
                'superuser',
            )

    def test_document_view_shared_owner_constraint(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            DocumentView.objects.create(
                project=self.project,
                user=self.user_engineer,
                name='Общий с владельцем',
                is_shared=True,
            )

        with self.assertRaises(IntegrityError), transaction.atomic():
            DocumentView.objects.create(
                project=self.project,
                user=None,
                name='Личный без владельца',
                is_shared=False,
            )

        shared = DocumentView.objects.create(
            project=self.project,
            user=None,
            name='Корректный общий',
            is_shared=True,
        )
        self.assertIsNone(shared.user)

    def test_last_gip_cannot_be_demoted(self):
        self.project.gip = None
        self.project.save(update_fields=['gip'])
        membership = ProjectMembership.objects.create(
            project=self.project,
            user=self.user_gip,
            role_in_project='gip',
        )

        with self.assertRaises(ValueError):
            update_project_member_role(self.user_admin, membership, 'viewer')

        membership.refresh_from_db()
        self.assertEqual(membership.role_in_project, 'gip')

    def test_gip_can_be_demoted_when_another_gip_exists(self):
        self.project.gip = None
        self.project.save(update_fields=['gip'])
        first = ProjectMembership.objects.create(
            project=self.project,
            user=self.user_gip,
            role_in_project='gip',
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user_admin,
            role_in_project='gip',
        )

        update_project_member_role(self.user_admin, first, 'viewer')
        first.refresh_from_db()
        self.assertEqual(first.role_in_project, 'viewer')

    def test_contractor_with_gip_rights_cannot_manage(self):
        membership = ProjectMembership.objects.create(
            project=self.project,
            user=self.user_contractor,
            role_in_project='gip',
        )
        self.project.gip = self.user_contractor
        self.project.save(update_fields=['gip'])

        self.assertFalse(can_manage_project_participants(self.user_contractor, self.project))
        with self.assertRaises(PermissionDenied):
            save_document_view(
                self.user_contractor,
                self.project,
                'Общий',
                {'columns': ['code']},
                is_shared=True,
            )
        with self.assertRaises(PermissionDenied):
            list_project_participants(self.user_contractor, self.project)
        with self.assertRaises(PermissionDenied):
            update_project_member_role(self.user_contractor, membership, 'viewer')

    def test_participant_services_permissions(self):
        participants = list_project_participants(self.user_gip, self.project)
        self.assertEqual(len(participants), 2)
        self.assertEqual(
            get_project_membership(
                self.user_gip,
                self.project,
                self.membership_engineer.pk,
            ),
            self.membership_engineer,
        )
        self.assertIsNone(
            get_project_membership(self.user_gip, self.project, 999999),
        )

        with self.assertRaises(PermissionDenied):
            list_project_participants(self.user_engineer, self.project)
        with self.assertRaises(PermissionDenied):
            get_project_membership(
                self.user_engineer,
                self.project,
                self.membership_engineer.pk,
            )


class GuiViewTests(GuiTestBase):
    def test_project_detail_can_manage_participants(self):
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'tab': 'participants'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_manage_participants'])
        self.assertIn('participants', response.context)
        self.assertEqual(len(response.context['participants']), 2)
        self.assertContains(response, 'Участники')
        self.assertContains(response, 'Изменить роль')

    def test_admin_can_manage_participants(self):
        self.client.force_login(self.user_admin)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'tab': 'participants'},
        )

        self.assertTrue(response.context['can_manage_participants'])
        self.assertContains(response, 'Изменить роль')

        self.client.post(
            reverse(
                'storage:membership_role_update',
                args=[self.project.pk, self.membership_viewer.pk],
            ),
            {'role': 'engineer'},
        )
        self.membership_viewer.refresh_from_db()
        self.assertEqual(self.membership_viewer.role_in_project, 'engineer')

    def test_participants_tab_hidden_for_non_managers(self):
        for user in (self.user_engineer, self.user_viewer, self.user_contractor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(
                    reverse('storage:project_detail', args=[self.project.pk]),
                )
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context['can_manage_participants'])
                self.assertNotIn('participants', response.context)
                self.assertNotContains(response, 'Изменить роль')

    def test_section_counts_rendered(self):
        self.client.force_login(self.user_gip)
        response = self.client.get(reverse('storage:project_detail', args=[self.project.pk]))

        self.assertContains(
            response,
            f'data-section-id="{self.section_ar.pk}" data-role="section-count">2</span>',
        )
        self.assertContains(
            response,
            f'data-section-id="{self.section_kr.pk}" data-role="section-count">1</span>',
        )

    def test_ui_refactor_structure(self):
        self.client.force_login(self.user_gip)
        response = self.client.get(reverse('storage:project_detail', args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-role="sidebar"')
        self.assertContains(response, 'data-role="toolbar"')
        self.assertContains(response, 'data-role="documents-table"')
        self.assertContains(response, 'data-role="settings-modal"')
        self.assertContains(response, 'id="quickFilter"')
        self.assertContains(response, 'id="openSettings"')
        self.assertContains(response, 'id="viewWidthsInput"')
        self.assertContains(response, 'id="sidebarCollapse"')
        self.assertContains(response, 'table-bordered')
        self.assertContains(response, 'id="documentsTable"')
        self.assertNotContains(response, 'col-resizer')
        self.assertContains(response, 'id="uploadBtn"')
        self.assertNotContains(response, 'Перетащите файлы сюда')
        self.assertNotContains(response, 'upload-zone')

    def test_scotty_table_filters(self):
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'status': 'approved'},
        )

        table = response.context['table']
        self.assertEqual(
            [row.record.code for row in table.rows],
            ['АР-02'],
        )
        self.assertEqual(table.paginator.per_page, 50)
        self.assertContains(response, 'data-role="documents-table"')

        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'code': 'АР-01'},
        )
        self.assertEqual(
            [row.record.code for row in response.context['table'].rows],
            ['АР-01'],
        )

    def test_scotty_table_pagination(self):
        Document.objects.bulk_create([
            Document(
                project=self.project,
                section=self.section_ar,
                code=f'P-{index:03}',
                name=f'Документ {index}',
                doc_type='drawing',
                status='draft',
                created_by=self.user_gip,
            )
            for index in range(51)
        ])
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk])
        )

        table = response.context['table']
        self.assertEqual(table.paginator.num_pages, 2)
        self.assertEqual(len(table.paginated_rows), 50)
        self.assertContains(response, 'data-role="pagination"')
        self.assertContains(response, 'page=2')

    def test_scotty_export_xlsx(self):
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'_export': 'xlsx'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'spreadsheetml',
            response['Content-Type'],
        )

    def test_save_view_via_post(self):
        self.client.force_login(self.user_engineer)
        response = self.client.post(
            reverse('storage:document_view_save', args=[self.project.pk]),
            {
                'name': 'Только шифры',
                'columns': ['code', 'status'],
                'status': 'approved',
                'sort': 'code',
            },
        )

        view = DocumentView.objects.get(
            project=self.project,
            user=self.user_engineer,
            name='Только шифры',
        )
        self.assertFalse(view.is_shared)
        self.assertEqual(view.config['columns'], ['code', 'status'])
        self.assertEqual(view.config['filters'], {'status': 'approved'})
        self.assertEqual(view.config['sort'], ['code'])
        self.assertRedirects(
            response,
            f"{reverse('storage:project_detail', args=[self.project.pk])}?view={view.pk}",
        )

    def test_shared_view_post_forbidden_for_engineer(self):
        self.client.force_login(self.user_engineer)
        response = self.client.post(
            reverse('storage:document_view_save', args=[self.project.pk]),
            {
                'name': 'Общий вид',
                'columns': ['code'],
                'is_shared': 'on',
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(DocumentView.objects.count(), 0)

    def test_shared_view_post_forbidden_for_contractor_with_gip_membership(self):
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user_contractor,
            role_in_project='gip',
        )
        self.client.force_login(self.user_contractor)
        response = self.client.post(
            reverse('storage:document_view_save', args=[self.project.pk]),
            {
                'name': 'Общий вид',
                'columns': ['code'],
                'is_shared': 'on',
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(DocumentView.objects.count(), 0)

    def test_contractor_with_gip_membership_has_no_participants_tab(self):
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user_contractor,
            role_in_project='gip',
        )
        self.client.force_login(self.user_contractor)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'tab': 'participants'},
        )

        self.assertFalse(response.context['can_manage_participants'])
        self.assertNotIn('participants', response.context)
        self.assertNotContains(response, 'Изменить роль')

    def test_corrupted_saved_view_config_falls_back(self):
        view = DocumentView.objects.create(
            project=self.project,
            user=self.user_engineer,
            name='Сломанный вид',
            config={'columns': ['unknown']},
            is_shared=False,
        )
        self.client.force_login(self.user_engineer)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'view': view.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['selected_view'])
        self.assertEqual(len(response.context['visible_columns']), 7)

    def test_saved_view_applied_columns(self):
        view = save_document_view(
            self.user_engineer,
            self.project,
            'Только шифры',
            {'columns': ['code']},
        )
        self.client.force_login(self.user_engineer)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'view': view.pk},
        )

        self.assertEqual(response.context['selected_view'], view)
        self.assertEqual(
            [column['key'] for column in response.context['visible_columns']],
            ['code'],
        )

    def test_invalid_filter_params_fall_back(self):
        self.client.force_login(self.user_gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'apply': '1', 'section': 'abc', 'sort': 'bad'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_filters'], {})
        self.assertEqual(len(response.context['documents']), 3)

    def test_saved_view_applied_filters(self):
        view = save_document_view(
            self.user_engineer,
            self.project,
            'Утверждённые',
            {'columns': ['code'], 'filters': {'status': 'approved'}},
        )
        self.client.force_login(self.user_engineer)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'view': view.pk},
        )

        self.assertEqual(
            [document['code'] for document in response.context['documents']],
            ['АР-02'],
        )

    def test_foreign_users_view_is_not_applied(self):
        view = save_document_view(
            self.user_engineer,
            self.project,
            'Личный',
            {'columns': ['code']},
        )
        self.client.force_login(self.user_viewer)
        response = self.client.get(
            reverse('storage:project_detail', args=[self.project.pk]),
            {'view': view.pk},
        )

        self.assertIsNone(response.context['selected_view'])
        self.assertEqual(len(response.context['visible_columns']), 7)

    def test_membership_role_update_view(self):
        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse(
                'storage:membership_role_update',
                args=[self.project.pk, self.membership_engineer.pk],
            ),
            {'role': 'viewer'},
        )

        self.assertEqual(response.status_code, 302)
        self.membership_engineer.refresh_from_db()
        self.assertEqual(self.membership_engineer.role_in_project, 'viewer')

    def test_membership_role_update_forbidden_for_engineer(self):
        self.client.force_login(self.user_engineer)
        response = self.client.post(
            reverse(
                'storage:membership_role_update',
                args=[self.project.pk, self.membership_viewer.pk],
            ),
            {'role': 'engineer'},
        )

        self.assertEqual(response.status_code, 403)
        self.membership_viewer.refresh_from_db()
        self.assertEqual(self.membership_viewer.role_in_project, 'viewer')

    def test_membership_role_update_rejects_invalid_role(self):
        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse(
                'storage:membership_role_update',
                args=[self.project.pk, self.membership_viewer.pk],
            ),
            {'role': 'superuser'},
            follow=True,
        )

        self.assertContains(response, 'Неверная роль участника')
        self.membership_viewer.refresh_from_db()
        self.assertEqual(self.membership_viewer.role_in_project, 'viewer')

    def test_membership_role_update_forbidden_for_contractor_with_gip_membership(self):
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user_contractor,
            role_in_project='gip',
        )
        self.client.force_login(self.user_contractor)
        response = self.client.post(
            reverse(
                'storage:membership_role_update',
                args=[self.project.pk, self.membership_viewer.pk],
            ),
            {'role': 'engineer'},
        )

        self.assertEqual(response.status_code, 403)
        self.membership_viewer.refresh_from_db()
        self.assertEqual(self.membership_viewer.role_in_project, 'viewer')

    def test_membership_role_update_missing_membership_returns_404(self):
        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse(
                'storage:membership_role_update',
                args=[self.project.pk, 999999],
            ),
            {'role': 'viewer'},
        )

        self.assertEqual(response.status_code, 404)

    def test_foreign_membership_update_returns_404(self):
        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse(
                'storage:membership_role_update',
                args=[self.foreign_project.pk, self.membership_viewer.pk],
            ),
            {'role': 'viewer'},
        )

        self.assertEqual(response.status_code, 404)
