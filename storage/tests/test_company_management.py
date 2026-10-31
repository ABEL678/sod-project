from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from storage.domain.models import Company, UserProfile
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

    def test_get_company_members(self):
        members = get_company_members(self.company)
        self.assertEqual(members.count(), 2)
        self.assertEqual(
            set(members.values_list('user__username', flat=True)),
            {'admin_a', 'dev_a'},
        )

    def test_add_company_member(self):
        user = User.objects.create_user('new_dev', password='pass')
        profile = add_company_member(
            self.company, user, 'developer', allowed_sections=[]
        )
        self.assertEqual(profile.company, self.company)
        self.assertEqual(profile.role, 'developer')
        self.assertTrue(
            UserProfile.objects.filter(
                user=user, company=self.company, role='developer'
            ).exists()
        )

    def test_remove_company_member(self):
        remove_company_member(self.company, self.developer)
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
