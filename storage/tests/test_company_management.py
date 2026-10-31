from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from storage.domain.models import Company, Project, Section, UserProfile
from storage.domain.services import (
    add_company_member,
    get_company_members,
    remove_company_member,
    update_company_info,
)


User = get_user_model()


class CompanyManagementServiceTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Компания А')
        self.admin = User.objects.create_user('admin_a', password='pass')
        UserProfile.objects.create(
            user=self.admin, company=self.company, role='admin'
        )
        self.developer = User.objects.create_user('dev_a', password='pass')
        UserProfile.objects.create(
            user=self.developer, company=self.company, role='developer'
        )
        self.gip = User.objects.create_user('gip_a', password='pass')
        UserProfile.objects.create(
            user=self.gip, company=self.company, role='gip'
        )

    def test_get_company_members(self):
        members = get_company_members(self.company)
        self.assertEqual(members.count(), 3)
        self.assertEqual(
            set(members.values_list('user__username', flat=True)),
            {'admin_a', 'dev_a', 'gip_a'},
        )

    def test_add_company_member(self):
        user = User.objects.create_user('new_dev', password='pass')
        profile = add_company_member(
            self.admin, self.company, user, 'developer', allowed_sections=[]
        )
        self.assertEqual(profile.company, self.company)
        self.assertEqual(profile.role, 'developer')
        self.assertTrue(
            UserProfile.objects.filter(
                user=user, company=self.company, role='developer'
            ).exists()
        )

    def test_admin_cannot_add_user_from_other_company(self):
        other_company = Company.objects.create(name='Компания Б')
        other_user = User.objects.create_user('other_admin', password='pass')
        UserProfile.objects.create(
            user=other_user, company=other_company, role='admin'
        )
        with self.assertRaises(PermissionDenied):
            add_company_member(
                self.admin, self.company, other_user, 'developer'
            )

    def test_admin_cannot_assign_section_from_other_company(self):
        other_company = Company.objects.create(name='Компания В')
        other_project = Project.objects.create(
            company=other_company,
            code='OTHER-1',
            name='Чужой проект',
            status='active',
            created_by=self.admin,
        )
        other_section = Section.objects.create(
            project=other_project,
            code='АР',
            name='Чужие архитектурные решения',
            order=1,
        )
        new_user = User.objects.create_user('section_dev', password='pass')
        with self.assertRaises(ValueError):
            add_company_member(
                self.admin,
                self.company,
                new_user,
                'developer',
                allowed_sections=[other_section],
            )

    def test_add_user_without_company_works(self):
        user = User.objects.create_user('free_user', password='pass')
        profile = add_company_member(
            self.admin, self.company, user, 'developer', allowed_sections=[]
        )
        self.assertEqual(profile.company, self.company)
        self.assertEqual(profile.role, 'developer')

    def test_gip_cannot_assign_admin(self):
        user = User.objects.create_user('gip_target', password='pass')
        with self.assertRaises(PermissionDenied):
            add_company_member(self.gip, self.company, user, 'admin')

    def test_gip_cannot_remove_admin(self):
        with self.assertRaises(PermissionDenied):
            remove_company_member(self.gip, self.company, self.admin)
        self.admin.userprofile.refresh_from_db()
        self.assertEqual(self.admin.userprofile.company, self.company)

    def test_admin_can_assign_admin(self):
        user = User.objects.create_user('admin_target', password='pass')
        profile = add_company_member(
            self.admin, self.company, user, 'admin'
        )
        self.assertEqual(profile.role, 'admin')
        self.assertEqual(profile.company, self.company)

    def test_superuser_can_assign_admin(self):
        root = User.objects.create_superuser('root_admin', password='pass')
        user = User.objects.create_user('super_target', password='pass')
        profile = add_company_member(root, self.company, user, 'admin')
        self.assertEqual(profile.role, 'admin')
        self.assertEqual(profile.company, self.company)

    def test_remove_company_member(self):
        remove_company_member(self.admin, self.company, self.developer)
        self.developer.userprofile.refresh_from_db()
        self.assertIsNone(self.developer.userprofile.company)

    def test_update_company_info(self):
        update_company_info(self.company, name='Новая компания')
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, 'Новая компания')


class DashboardAccessTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Компания Б')
        self.developer = User.objects.create_user('dev_b', password='pass')
        UserProfile.objects.create(
            user=self.developer, company=self.company, role='developer'
        )

    def test_dashboard_access_denied_for_developer(self):
        self.client.force_login(self.developer)
        response = self.client.get(reverse('storage:dashboard'))
        self.assertEqual(response.status_code, 403)
