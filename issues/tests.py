import base64
import tempfile
from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from issues.models import Issue
from storage.domain.models import (
    Company,
    Document,
    Project,
    ProjectMembership,
    Section,
    UserProfile,
)
from storage.domain.services import (
    allowed_issue_statuses,
    check_read_permission,
    create_issue,
    delete_issue,
    get_issues_for_version,
    update_issue_answer,
    update_issue_status,
    upload_document,
)


User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class IssueTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Компания 1')

        self.user_gip = User.objects.create_user('gip', password='pass')
        UserProfile.objects.create(user=self.user_gip, role='gip', company=self.company)

        self.user_viewer = User.objects.create_user('viewer', password='pass')
        UserProfile.objects.create(user=self.user_viewer, role='viewer', company=self.company)

        self.project = Project.objects.create(
            company=self.company,
            code='P-1',
            name='Проект',
            status='active',
            gip=self.user_gip,
            created_by=self.user_gip,
        )
        self.section = Section.objects.create(
            project=self.project,
            code='АР',
            name='Архитектурные решения',
            order=1,
        )

        self.version = upload_document(
            user=self.user_gip,
            project=self.project,
            section=self.section,
            code='АР-01',
            name='План 1 этажа',
            doc_type='drawing',
            file=SimpleUploadedFile('plan.pdf', b'data', content_type='application/pdf'),
        )
        self.document = self.version.document

    def test_create_issue(self):
        self.client.force_login(self.user_gip)
        response = self.client.post(
            reverse('issues:issue_create', args=[self.document.pk]),
            {
                'description': 'Не указана отметка 0.000',
                'version_id': self.version.pk,
            },
        )
        self.assertRedirects(
            response,
            reverse('storage:document_detail', args=[self.document.pk]),
        )
        issue = Issue.objects.get()
        self.assertEqual(issue.document, self.document)
        self.assertEqual(issue.version, self.version)
        self.assertEqual(issue.author, self.user_gip)
        self.assertEqual(issue.status, 'NEW')

    def test_create_issue_sets_current_version_by_default(self):
        self.client.force_login(self.user_gip)
        self.client.post(
            reverse('issues:issue_create', args=[self.document.pk]),
            {'description': 'Замечание без явной версии'},
        )
        issue = Issue.objects.get()
        self.assertEqual(issue.version, self.document.current_version)

    def test_create_issue_requires_read_permission(self):
        contractor = User.objects.create_user('contractor', password='pass')
        UserProfile.objects.create(user=contractor, role='contractor', company=self.company)
        self.client.force_login(contractor)
        response = self.client.post(
            reverse('issues:issue_create', args=[self.document.pk]),
            {'description': 'Чужое замечание'},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Issue.objects.count(), 0)

    def test_create_issue_contractor_foreign_section_not_found(self):
        other_section = Section.objects.create(
            project=self.project,
            code='КР',
            name='Конструктивные решения',
            order=2,
        )
        other_document = Document.objects.create(
            project=self.project,
            section=other_section,
            code='КР-01',
            name='Разрез',
            doc_type='drawing',
            status='draft',
            created_by=self.user_gip,
        )
        contractor = User.objects.create_user('contractor2', password='pass')
        profile = UserProfile.objects.create(
            user=contractor,
            role='contractor',
            company=self.company,
        )
        profile.allowed_sections.add(self.section)
        self.client.force_login(contractor)

        response = self.client.post(
            reverse('issues:issue_create', args=[other_document.pk]),
            {'description': 'Чужое замечание'},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Issue.objects.count(), 0)

    def test_toggle_status(self):
        issue = Issue.objects.create(
            document=self.document,
            version=self.version,
            author=self.user_gip,
            description='Замечание',
        )
        self.client.force_login(self.user_gip)

        response = self.client.post(
            reverse('issues:issue_status', args=[issue.pk]),
            {'status': 'CLOSED'},
        )
        self.assertRedirects(
            response,
            reverse('storage:document_detail', args=[self.document.pk]),
        )
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'CLOSED')
        self.assertIsNotNone(issue.closed_at)
        self.assertEqual(issue.closed_by, self.user_gip)

        self.client.post(
            reverse('issues:issue_status', args=[issue.pk]),
            {'status': 'NEW'},
        )
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'NEW')
        self.assertIsNone(issue.closed_at)
        self.assertIsNone(issue.closed_by)

    def test_status_change_forbidden_for_stranger(self):
        issue = Issue.objects.create(
            document=self.document,
            version=self.version,
            author=self.user_gip,
            description='Замечание',
        )
        self.client.force_login(self.user_viewer)
        response = self.client.post(
            reverse('issues:issue_status', args=[issue.pk]),
            {'action': 'close'},
        )
        self.assertEqual(response.status_code, 403)
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'NEW')

    def test_status_change_contractor_foreign_section_not_found(self):
        other_section = Section.objects.create(
            project=self.project,
            code='КР',
            name='Конструктивные решения',
            order=2,
        )
        other_document = Document.objects.create(
            project=self.project,
            section=other_section,
            code='КР-02',
            name='Разрез',
            doc_type='drawing',
            status='draft',
            created_by=self.user_gip,
        )
        issue = Issue.objects.create(
            document=other_document,
            version=None,
            author=self.user_gip,
            description='Замечание в чужом разделе',
        )
        contractor = User.objects.create_user('contractor3', password='pass')
        profile = UserProfile.objects.create(
            user=contractor,
            role='contractor',
            company=self.company,
        )
        profile.allowed_sections.add(self.section)
        self.client.force_login(contractor)

        response = self.client.post(
            reverse('issues:issue_status', args=[issue.pk]),
            {'action': 'close'},
        )

        self.assertEqual(response.status_code, 404)
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'NEW')

    def test_create_issue_with_pin_coords(self):
        self.client.force_login(self.user_gip)
        issue = create_issue(
            user=self.user_gip,
            document=self.document,
            description='Замечание на пине',
            version=self.version,
            page_number=1,
            x_ratio=0.5,
            y_ratio=0.3,
        )
        self.assertEqual(issue.page_number, 1)
        self.assertEqual(issue.x_ratio, 0.5)
        self.assertEqual(issue.y_ratio, 0.3)

    def test_create_issue_x_y_without_page_number_raises(self):
        with self.assertRaises(ValueError):
            create_issue(
                user=self.user_gip,
                document=self.document,
                description='Координаты без номера страницы',
                page_number=None,
                x_ratio=0.5,
                y_ratio=0.5,
            )

    def test_create_issue_forbidden_for_viewer(self):
        with self.assertRaises(PermissionDenied):
            create_issue(
                user=self.user_viewer,
                document=self.document,
                description='Замечание без прав на запись',
            )

    def test_create_issue_view_forbidden_for_viewer(self):
        self.client.force_login(self.user_viewer)
        response = self.client.post(
            reverse('issues:issue_create', args=[self.document.pk]),
            {'description': 'Замечание без прав на запись'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Issue.objects.count(), 0)

    def test_create_issue_pin_coords_without_page_number(self):
        issue = create_issue(
            user=self.user_gip,
            document=self.document,
            description='Без пина',
        )
        self.assertIsNone(issue.page_number)
        self.assertIsNone(issue.x_ratio)
        self.assertIsNone(issue.y_ratio)

    def test_create_issue_missing_x_y_with_page_number(self):
        with self.assertRaises(ValueError):
            create_issue(
                user=self.user_gip,
                document=self.document,
                description='Пин без координат',
                page_number=1,
            )

    def test_create_issue_x_ratio_out_of_range(self):
        with self.assertRaises(ValueError):
            create_issue(
                user=self.user_gip,
                document=self.document,
                description='Координата вне диапазона',
                page_number=1,
                x_ratio=1.5,
                y_ratio=0.3,
            )

    def test_create_issue_version_not_from_document(self):
        other_section = Section.objects.create(
            project=self.project,
            code='КР',
            name='Конструктивные решения',
            order=2,
        )
        other_document = Document.objects.create(
            project=self.project,
            section=other_section,
            code='КР-99',
            name='Чужой документ',
            doc_type='drawing',
            status='draft',
            created_by=self.user_gip,
        )
        other_version = upload_document(
            user=self.user_gip,
            project=self.project,
            section=other_section,
            code='КР-99',
            name='Чужой документ',
            doc_type='drawing',
            file=SimpleUploadedFile('other.pdf', b'data', content_type='application/pdf'),
        )
        with self.assertRaises(ValueError):
            create_issue(
                user=self.user_gip,
                document=self.document,
                description='Версия от другого документа',
                version=other_version,
            )

@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class IssueStatusMatrixTests(TestCase):
    """T11: матрица статусов замечаний по ГОСТ-ролям."""

    def setUp(self):
        self.company = Company.objects.create(name='Матрица')
        self.gip = self._user('gip', 'gip')
        self.membership_gip = self._user('membership_gip', 'gip')
        self.developer = self._user('developer', 'developer')
        self.checker = self._user('checker', 'checker')
        self.normocontrol = self._user('normocontrol', 'normocontrol')
        self.customer = self._user('customer', 'customer')
        self.contractor = self._user('contractor', 'contractor')
        self.viewer = self._user('viewer', 'viewer')

        self.project = Project.objects.create(
            company=self.company,
            code='MATRIX-1',
            name='Матрица',
            status='active',
            gip=self.gip,
            created_by=self.gip,
        )
        self.section = Section.objects.create(
            project=self.project,
            code='АР',
            name='Архитектурные решения',
            order=1,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.developer,
            role_in_project='developer',
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.membership_gip,
            role_in_project='gip',
        )
        self.developer.userprofile.allowed_sections.add(self.section)
        self.contractor.userprofile.allowed_sections.add(self.section)

        self.document = upload_document(
            user=self.gip,
            project=self.project,
            section=self.section,
            code='АР-01',
            name='План',
            doc_type='drawing',
            file=SimpleUploadedFile('plan.pdf', b'data', content_type='application/pdf'),
        ).document

    def _user(self, username, role):
        user = User.objects.create_user(username, password='pass')
        UserProfile.objects.create(user=user, role=role, company=self.company)
        return user

    def _issue(self):
        return create_issue(
            user=self.gip,
            document=self.document,
            description='Замечание',
        )

    def test_developer_can_set_work_statuses(self):
        for status in ('IN_PROGRESS', 'DISCUSSION', 'DONE'):
            issue = self._issue()
            update_issue_status(self.developer, issue, status)
            issue.refresh_from_db()
            self.assertEqual(issue.status, status)

    def test_developer_cannot_close_or_annul(self):
        for status in ('CLOSED', 'CANCELLED', 'REJECTED', 'NEW'):
            issue = self._issue()
            with self.assertRaises(PermissionDenied):
                update_issue_status(self.developer, issue, status)

    def test_developer_in_progress_assigns_issue(self):
        issue = self._issue()
        update_issue_status(self.developer, issue, 'IN_PROGRESS')
        issue.refresh_from_db()
        self.assertEqual(issue.assignee, self.developer)

    def test_checker_cannot_set_in_progress(self):
        issue = self._issue()
        with self.assertRaises(PermissionDenied):
            update_issue_status(self.checker, issue, 'IN_PROGRESS')

    def test_checker_can_reject_close_and_cancel(self):
        for status in ('REJECTED', 'CLOSED', 'CANCELLED'):
            issue = self._issue()
            update_issue_status(self.checker, issue, status)
            issue.refresh_from_db()
            self.assertEqual(issue.status, status)
            if status in ('CLOSED', 'CANCELLED'):
                self.assertIsNotNone(issue.closed_at)
                self.assertEqual(issue.closed_by, self.checker)

    def test_normocontrol_can_close(self):
        issue = self._issue()
        update_issue_status(self.normocontrol, issue, 'CLOSED')
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'CLOSED')

    def test_customer_can_create_issue(self):
        issue = create_issue(
            user=self.customer,
            document=self.document,
            description='Замечание заказчика',
        )
        self.assertEqual(issue.author, self.customer)
        self.assertEqual(issue.status, 'NEW')

    def test_customer_cannot_upload_files(self):
        with self.assertRaises(PermissionDenied):
            upload_document(
                user=self.customer,
                project=self.project,
                section=self.section,
                code='АР-02',
                name='План 2',
                doc_type='drawing',
                file=SimpleUploadedFile('plan2.pdf', b'data', content_type='application/pdf'),
            )

    def test_contractor_can_create_issue_in_allowed_section(self):
        issue = create_issue(
            user=self.contractor,
            document=self.document,
            description='Замечание смежника',
        )
        self.assertEqual(issue.author, self.contractor)

    def test_gip_can_set_any_status(self):
        for status, _ in Issue.STATUS_CHOICES:
            issue = self._issue()
            update_issue_status(self.gip, issue, status)
            issue.refresh_from_db()
            self.assertEqual(issue.status, status)

    def test_allowed_statuses_for_roles(self):
        issue = self._issue()
        self.assertEqual(
            set(allowed_issue_statuses(self.developer, issue)),
            {'IN_PROGRESS', 'DISCUSSION', 'DONE'},
        )
        self.assertEqual(
            set(allowed_issue_statuses(self.checker, issue)),
            {'REJECTED', 'CLOSED', 'CANCELLED'},
        )
        self.assertEqual(allowed_issue_statuses(self.viewer, issue), [])

    def test_document_detail_status_buttons_follow_matrix(self):
        self._issue()
        self.client.force_login(self.developer)
        response = self.client.get(
            reverse('storage:document_detail', args=[self.document.pk])
        )
        self.assertContains(response, 'value="DONE"')
        self.assertNotContains(response, 'value="CLOSED"')

        self.client.force_login(self.checker)
        response = self.client.get(
            reverse('storage:document_detail', args=[self.document.pk])
        )
        self.assertContains(response, 'value="CLOSED"')
        self.assertNotContains(response, 'value="IN_PROGRESS"')

    def test_viewer_does_not_see_issue_create_form(self):
        self.client.force_login(self.viewer)
        response = self.client.get(
            reverse('storage:document_detail', args=[self.document.pk])
        )
        self.assertNotContains(
            response,
            reverse('issues:issue_create', args=[self.document.pk]),
        )

    def test_membership_gip_can_read_and_change_issue_status(self):
        self.assertTrue(check_read_permission(self.membership_gip, self.document))
        issue = self._issue()
        update_issue_status(self.membership_gip, issue, 'CLOSED')
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'CLOSED')
        self.assertEqual(issue.closed_by, self.membership_gip)

    def test_developer_cannot_reopen_closed_issue(self):
        issue = self._issue()
        update_issue_status(self.gip, issue, 'CLOSED')
        with self.assertRaises(PermissionDenied):
            update_issue_status(self.developer, issue, 'IN_PROGRESS')
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'CLOSED')

        update_issue_status(self.gip, issue, 'IN_PROGRESS')
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'IN_PROGRESS')
        self.assertIsNone(issue.closed_at)

    def test_developer_done_transitions(self):
        for target in ('DISCUSSION', 'IN_PROGRESS'):
            issue = self._issue()
            update_issue_status(self.developer, issue, 'DONE')
            update_issue_status(self.developer, issue, target)
            issue.refresh_from_db()
            self.assertEqual(issue.status, target)

    def test_ajax_create_issue_returns_issue_with_pin(self):
        self.client.force_login(self.customer)
        response = self.client.post(
            reverse('issues:issue_create', args=[self.document.pk]),
            {
                'description': 'Замечание с пина',
                'page_number': '1',
                'x_ratio': '0.25',
                'y_ratio': '0.75',
                'assignee': str(self.developer.pk),
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload['status'], 'ok')
        issue = Issue.objects.get(pk=payload['issue']['id'])
        self.assertEqual(issue.page_number, 1)
        self.assertAlmostEqual(issue.x_ratio, 0.25)
        self.assertAlmostEqual(issue.y_ratio, 0.75)
        self.assertEqual(issue.assignee, self.developer)
        self.assertEqual(payload['issue']['status'], 'NEW')

    def test_get_issues_for_version_filters(self):
        version = self.document.current_version
        versioned = create_issue(
            user=self.gip,
            document=self.document,
            description='К версии',
            version=version,
        )
        create_issue(
            user=self.gip,
            document=self.document,
            description='Без версии',
        )
        ids = list(
            get_issues_for_version(self.document, version).values_list('pk', flat=True)
        )
        self.assertEqual(ids, [versioned.pk])

    def test_create_issue_with_markup_crop_and_details(self):
        crop = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8\xff\xd9').decode()
        issue = create_issue(
            user=self.gip,
            document=self.document,
            description='Маркап',
            version=self.document.current_version,
            page_number=1,
            x1_ratio=0.1,
            y1_ratio=0.2,
            x2_ratio=0.4,
            y2_ratio=0.5,
            crop_image_base64=crop,
            department='АР',
            project_organization='ООО Проект',
            answer_text='Ответ проектировщика',
            due_date=date(2026, 11, 1),
            revision='R2',
        )
        issue.refresh_from_db()
        self.assertAlmostEqual(issue.x1_ratio, 0.1)
        self.assertAlmostEqual(issue.x2_ratio, 0.4)
        self.assertAlmostEqual(issue.x_ratio, 0.25)
        self.assertAlmostEqual(issue.y_ratio, 0.35)
        self.assertEqual(issue.department, 'АР')
        self.assertEqual(issue.project_organization, 'ООО Проект')
        self.assertEqual(issue.answer_text, 'Ответ проектировщика')
        self.assertEqual(issue.due_date, date(2026, 11, 1))
        self.assertEqual(issue.revision, 'R2')
        self.assertTrue(issue.crop_image)
        self.assertTrue(issue.crop_image.storage.exists(issue.crop_image.name))

    def test_ajax_create_with_markup_and_crop(self):
        self.client.force_login(self.customer)
        crop = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8\xff\xd9').decode()
        response = self.client.post(
            reverse('issues:issue_create', args=[self.document.pk]),
            {
                'description': 'Маркап заказчика',
                'page_number': '1',
                'x1_ratio': '0.2',
                'y1_ratio': '0.2',
                'x2_ratio': '0.5',
                'y2_ratio': '0.6',
                'crop_image_base64': crop,
                'department': 'Заказчик',
                'project_organization': 'ООО Заказчик',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()['issue']
        self.assertEqual(payload['department'], 'Заказчик')
        self.assertEqual(payload['project_organization'], 'ООО Заказчик')
        self.assertTrue(payload['crop_image_url'])
        self.assertIn('status_options', payload)

    def test_answer_update_and_delete_permissions(self):
        issue = self._issue()
        update_issue_answer(self.checker, issue, 'Ответ проверяющего')
        issue.refresh_from_db()
        self.assertEqual(issue.answer_text, 'Ответ проверяющего')

        with self.assertRaises(PermissionDenied):
            delete_issue(self.checker, issue)

        delete_issue(self.gip, issue)
        self.assertFalse(Issue.objects.filter(pk=issue.pk).exists())

    def test_done_sets_actual_fix_date(self):
        issue = self._issue()
        update_issue_status(self.developer, issue, 'DONE')
        issue.refresh_from_db()
        self.assertIsNotNone(issue.actual_fix_date)

    def test_document_detail_has_pdf_toolbar_and_element_tab(self):
        self._issue()
        self.client.force_login(self.gip)
        response = self.client.get(
            reverse('storage:document_detail', args=[self.document.pk])
        )
        self.assertContains(response, 'data-pdf-action="zoom-in"')
        self.assertContains(response, 'id="issueElementTab"')
        self.assertContains(response, 'id="issueFilesTab"')
        self.assertContains(response, 'id="issueContextMenu"')
        self.assertContains(response, 'id="detailDepartment"')
        self.assertContains(response, 'id="detailOrganization"')
