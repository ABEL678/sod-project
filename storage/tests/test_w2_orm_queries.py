"""P2 (Аудит 5.0): остаточные ORM-запросы реестра проекта.

Проверяем не абсолютное время и не «магическое» число, а отсутствие
роста запросов при увеличении дерева/списка документов — именно это
давало остаточный N+1 на странице реестра.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from issues.context_processors import notifications_badges
from issues.models import Notification
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
    _clear_profile_cache,
    _ensure_project_access,
    _is_project_gip,
    _workflow_role_allowed,
    attach_user_to_company,
    transition_document_zone,
)
from storage.tests.test_sprint5 import Sprint5TestBase

User = get_user_model()


class AccessAndGipQueryTests(TestCase):
    """Точечные проверки кэша доступа, префетча состава и прав workflow."""

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Компания P2')
        cls.root = User.objects.create_superuser(
            'root_p2', email='root_p2@example.com', password='pass',
        )
        cls.project = Project.objects.create(
            company=cls.company,
            code='P2-ACCESS',
            name='Проект P2',
            status='active',
            created_by=cls.root,
        )

    def test_project_access_checked_once_per_user_instance(self):
        user = User.objects.get(pk=self.root.pk)
        with CaptureQueriesContext(connection) as ctx:
            for _ in range(6):
                _ensure_project_access(user, self.project)
        # superuser: ровно один EXISTS, остальные обращения — из кэша.
        self.assertEqual(len(ctx), 1)

    def test_project_access_cached_for_company_user(self):
        user = User.objects.create_user('company_p2', password='pass')
        UserProfile.objects.update_or_create(
            user=user,
            defaults={'company': self.company, 'role': 'viewer'},
        )
        # Профиль и компанию читаем заранее: вне capture остаётся только EXISTS.
        self.assertEqual(user.userprofile.company_id, self.company.pk)
        self.assertIsNotNone(user.userprofile.company)
        with CaptureQueriesContext(connection) as ctx:
            for _ in range(5):
                _ensure_project_access(user, self.project)
        self.assertEqual(len(ctx), 1)

    def test_clear_profile_cache_invalidates_project_access(self):
        user = User.objects.create_user('reset_p2', password='pass')
        UserProfile.objects.update_or_create(
            user=user,
            defaults={'company': self.company, 'role': 'viewer'},
        )
        self.assertEqual(user.userprofile.company_id, self.company.pk)
        self.assertIsNotNone(user.userprofile.company)
        with CaptureQueriesContext(connection) as ctx:
            _ensure_project_access(user, self.project)
        self.assertEqual(len(ctx), 1)

        _clear_profile_cache(user)
        with CaptureQueriesContext(connection) as ctx:
            _ensure_project_access(user, self.project)
        # После сброса перечитываются профиль и компания, затем EXISTS.
        self.assertEqual(len(ctx), 3)

    def test_denied_project_not_cached_as_allowed_after_company_change(self):
        other_company = Company.objects.create(name='Другая компания P2')
        user = User.objects.create_user('moved_p2', password='pass')
        UserProfile.objects.update_or_create(
            user=user,
            defaults={'company': other_company, 'role': 'viewer'},
        )
        with self.assertRaises(PermissionDenied):
            _ensure_project_access(user, self.project)

        attach_user_to_company(
            user, self.company, role='viewer', actor=self.root,
        )
        _ensure_project_access(user, self.project)

    def test_is_project_gip_reads_prefetched_memberships(self):
        gip = User.objects.create_user('gip_p2', password='pass')
        ProjectMembership.objects.create(
            project=self.project, user=gip, role_in_project='gip',
        )
        project = Project.objects.prefetch_related('memberships').get(
            pk=self.project.pk,
        )
        with CaptureQueriesContext(connection) as ctx:
            for _ in range(4):
                self.assertTrue(_is_project_gip(gip, project))
        self.assertEqual(len(ctx), 0)

    def test_workflow_role_allowed_reads_prefetched_checker_membership(self):
        """M1: checker/normocontrol не даёт ORM-запроса на префетчнутой связи."""
        checker = User.objects.create_user('checker_p2', password='pass')
        UserProfile.objects.update_or_create(
            user=checker,
            defaults={'company': self.company, 'role': 'checker'},
        )
        ProjectMembership.objects.create(
            project=self.project, user=checker, role_in_project='checker',
        )
        section = Section.objects.create(
            project=self.project, code='АР', name='Архитектура', order=1,
        )
        document = Document.objects.create(
            project=self.project,
            section=section,
            code='P2-CHK',
            name='Документ проверяющего',
            doc_type='drawing',
            created_by=self.root,
        )
        prefetched = Document.objects.select_related(
            'project',
        ).prefetch_related(
            'project__memberships',
        ).get(pk=document.pk)
        # Прогрев кэша компании на инстансе пользователя — вне замера.
        self.assertTrue(
            _workflow_role_allowed(checker, prefetched, 'return_wip'),
        )
        with CaptureQueriesContext(connection) as ctx:
            for _ in range(4):
                self.assertTrue(
                    _workflow_role_allowed(checker, prefetched, 'return_wip'),
                )
        self.assertEqual(len(ctx), 0)

    def _shared_workflow_document(self):
        """Документ на этапе SHARED — цель действия «Вернуть в работу»."""
        section = Section.objects.create(
            project=self.project, code='P2-WF', name='Workflow', order=1,
        )
        return Document.objects.create(
            project=self.project,
            section=section,
            code='P2-WF-DOC',
            name='Документ workflow',
            doc_type='drawing',
            state=Document.STATE_SHARED,
            created_by=self.root,
        )

    def _checker(self, username):
        user = User.objects.create_user(username, password='pass')
        UserProfile.objects.update_or_create(
            user=user,
            defaults={'company': self.company, 'role': 'checker'},
        )
        return user

    def test_checker_with_viewer_membership_cannot_return_from_shared(self):
        """M1: membership role_in_project='viewer' не даёт действие проверяющего."""
        checker = self._checker('checker_viewer_p2')
        ProjectMembership.objects.create(
            project=self.project, user=checker, role_in_project='viewer',
        )
        document = self._shared_workflow_document()

        with self.assertRaises(PermissionDenied):
            transition_document_zone(checker, document, 'return_wip')

        document.refresh_from_db()
        self.assertEqual(document.state, Document.STATE_SHARED)

    def test_checker_not_in_project_cannot_return_from_shared(self):
        """M1: без членства в проекте действие проверяющего недоступно."""
        checker = self._checker('checker_outsider_p2')
        document = self._shared_workflow_document()

        with self.assertRaises(PermissionDenied):
            transition_document_zone(checker, document, 'return_wip')

        document.refresh_from_db()
        self.assertEqual(document.state, Document.STATE_SHARED)

    def test_notifications_badges_queries_only_once_per_request(self):
        user = User.objects.create_user('notify_p2', password='pass')
        Notification.objects.create(
            recipient=user,
            verb='assigned',
            message='Тестовое уведомление',
        )
        request = RequestFactory().get('/')
        request.user = user
        with CaptureQueriesContext(connection) as ctx:
            first = notifications_badges(request)
            second = notifications_badges(request)
        self.assertEqual(first, second)
        # unread + assigned + created — и ни одного повтора при втором вызове.
        self.assertEqual(len(ctx), 3)


class RegistryQueryScalingTests(Sprint5TestBase):
    """Страница реестра не должна расти от числа папок и документов."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user_gip)
        self.url = reverse(
            'storage:project_detail', args=[self.project.pk],
        ) + '?tab=documents'

    def _page_queries(self):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
        return len(ctx)

    def test_query_count_does_not_grow_with_folders(self):
        baseline = self._page_queries()
        for index in range(6):
            Folder.objects.create(
                project=self.project,
                section=self.section_ar,
                name=f'Доп. папка {index}',
                zone='WIP',
                created_by=self.user_gip,
            )
        self.assertEqual(self._page_queries(), baseline)

    def test_query_count_does_not_grow_with_documents(self):
        self.make_document(code='P2-DOC-1')
        baseline = self._page_queries()
        for index in range(2, 6):
            self.make_document(code=f'P2-DOC-{index}')
        self.assertEqual(self._page_queries(), baseline)


