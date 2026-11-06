"""Спринт 8, задача 7: автосоздание UserProfile и привязка к компании.

- сигнал post_save на User создаёт профиль каждому новому пользователю;
- superuser привязывается к первой компании (без компаний — создаётся
  «Stream-CAD»; на мигрированной БД первая компания уже есть — «Моя
  компания» из миграции 0005); обычный пользователь остаётся без
  компании (Аудит 6.0) — привязку делает админ через Панель управления;
- attach_user_to_company: права admin компании / superuser;
- AttachCompanyView: UI-привязка в Панели управления.
"""
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from storage.domain.models import Company, UserProfile
from storage.domain.services import attach_user_to_company, get_my_company
from storage.domain.signals import create_user_profile


class UserProfileSignalTests(TestCase):
    """Сигнал post_save: профиль, компания и роль у новых пользователей."""

    def test_superuser_binds_to_first_company_as_admin(self):
        # На мигрированной БД первая компания уже есть («Моя компания»,
        # миграция 0005). Superuser должен привязаться к ней с ролью admin.
        Company.objects.create(name='Альфа')  # НЕ первая (pk больше)
        user = User.objects.create_superuser(
            username='root', email='root@example.com', password='SuperPass123',
        )
        profile = UserProfile.objects.get(user=user)
        expected = Company.objects.order_by('pk').first()
        self.assertEqual(profile.company_id, expected.pk)
        self.assertEqual(profile.role, 'admin')

    def test_superuser_creates_stream_cad_when_no_companies(self):
        # Защитная ветка сигнала: БД без компаний (нестандартный кейс).
        Company.objects.all().delete()
        user = User.objects.create_superuser(
            username='root2', email='root2@example.com', password='SuperPass123',
        )
        profile = UserProfile.objects.get(user=user)
        self.assertIsNotNone(profile.company)
        self.assertEqual(profile.company.name, 'Stream-CAD')
        self.assertEqual(profile.role, 'admin')

    def test_regular_user_gets_bare_profile_with_existing_company(self):
        # Аудит 6.0: автопривязка к первой компании удалена. На мигрированной
        # БД первая компания — сидируемая «Моя компания» (миграция 0005):
        # прикрепляя каждого нового пользователя к ней, сигнал давал доступ
        # к чужим проектам и ломал добавление в настоящую компанию
        # (add_company_member: «Пользователь уже принадлежит другой компании»).
        # Компанию назначает админ через Панель управления.
        Company.objects.create(name='Альфа')
        user = User.objects.create_user(username='dev1', password='DevPass123')
        profile = UserProfile.objects.get(user=user)
        self.assertIsNone(profile.company)
        self.assertEqual(profile.role, 'viewer')

    def test_regular_user_without_companies_gets_bare_profile(self):
        # Не-superuser не создаёт компанию — профиль без привязки,
        # компанию назначает админ через Панель управления.
        Company.objects.all().delete()
        user = User.objects.create_user(username='dev2', password='DevPass123')
        profile = UserProfile.objects.get(user=user)
        self.assertIsNone(profile.company)
        self.assertEqual(profile.role, 'viewer')

    def test_signal_does_not_duplicate_profile(self):
        user = User.objects.create_user(username='dev3', password='DevPass123')
        # Эмуляция loaddata (raw=True): профиль не дублируется.
        create_user_profile(User, user, True, raw=True)
        self.assertEqual(UserProfile.objects.filter(user=user).count(), 1)

    def test_get_my_company(self):
        # Аудит 6.0: новые пользователи без компании (привязка — через
        # Панель управления), поэтому get_my_company возвращает None.
        Company.objects.create(name='Альфа')
        user = User.objects.create_user(username='dev4', password='DevPass123')
        self.assertIsNone(get_my_company(user))
        # Пользователь без профиля — None, без исключения.
        orphan = User(username='orphan')
        self.assertIsNone(get_my_company(orphan))


class AttachCompanyServiceTests(TestCase):
    """attach_user_to_company: права, роли, привязка."""

    def setUp(self):
        self.company = Company.objects.create(name='Альфа')
        self.admin = User.objects.create_superuser(
            username='root', email='root@example.com', password='SuperPass123',
        )
        # superuser — вне ограничений по компаниям (is_superuser).

    def test_admin_can_attach_user(self):
        target = User.objects.create_user(username='newbie', password='NewPass123')
        profile = attach_user_to_company(
            target, self.company, 'developer', actor=self.admin,
        )
        self.assertEqual(profile.company_id, self.company.pk)
        self.assertEqual(profile.role, 'developer')

    def test_company_admin_can_attach_user(self):
        company_admin = User.objects.create_user(
            username='admin1', password='AdminPass123',
        )
        company_admin.userprofile.role = 'admin'
        company_admin.userprofile.company = self.company
        company_admin.userprofile.save()
        target = User.objects.create_user(username='newbie', password='NewPass123')
        profile = attach_user_to_company(
            target, self.company, 'viewer', actor=company_admin,
        )
        self.assertEqual(profile.company_id, self.company.pk)

    def test_non_admin_gets_permission_denied(self):
        outsider = User.objects.create_user(
            username='outsider', password='OutPass123',
        )
        self.assertEqual(outsider.userprofile.role, 'viewer')
        target = User.objects.create_user(username='target', password='TarPass123')
        with self.assertRaises(PermissionDenied):
            attach_user_to_company(
                target, self.company, 'viewer', actor=outsider,
            )

    def test_admin_of_other_company_denied(self):
        other = User.objects.create_user(
            username='other_admin', password='OthPass123',
        )
        other_company = Company.objects.create(name='Бета')
        other.userprofile.role = 'admin'
        other.userprofile.company = other_company
        other.userprofile.save()
        target = User.objects.create_user(username='target', password='TarPass123')
        with self.assertRaises(PermissionDenied):
            attach_user_to_company(
                target, self.company, 'viewer', actor=other,
            )

    def test_invalid_role_raises_value_error(self):
        target = User.objects.create_user(username='newbie', password='NewPass123')
        with self.assertRaises(ValueError):
            attach_user_to_company(
                target, self.company, 'wizard', actor=self.admin,
            )

    def test_none_company_raises_value_error(self):
        target = User.objects.create_user(username='newbie', password='NewPass123')
        with self.assertRaises(ValueError):
            attach_user_to_company(target, None, 'viewer', actor=self.admin)


class AttachCompanyViewTests(TestCase):
    """UI-привязка: POST /dashboard/attach-company/."""

    def test_superuser_can_switch_company_via_ui(self):
        second = Company.objects.create(name='Вторая')
        root = User.objects.create_superuser(
            username='root', email='root@example.com', password='SuperPass123',
        )
        # Сигнал привязал superuser к первой компании («Моя компания»).
        self.assertNotEqual(root.userprofile.company_id, second.pk)
        self.client.force_login(root)
        response = self.client.post(
            reverse('storage:attach_company'), {'company_id': second.pk},
        )
        self.assertRedirects(response, reverse('storage:dashboard'))
        root.userprofile.refresh_from_db()
        self.assertEqual(root.userprofile.company_id, second.pk)

    def test_dashboard_without_company_shows_attach_form(self):
        root = User.objects.create_superuser(
            username='root2', email='root2@example.com', password='SuperPass123',
        )
        # Пока есть хоть одна компания, superuser видит первую (fallback
        # DashboardView.get_company). Убираем все — остаётся ветка
        # «Моя компания» (FK UserProfile.company = SET_NULL).
        Company.objects.all().delete()
        root.userprofile.refresh_from_db()
        self.assertIsNone(root.userprofile.company)
        self.client.force_login(root)
        response = self.client.get(reverse('storage:dashboard'))
        self.assertContains(response, 'Моя компания')
        self.assertContains(response, 'Привязаться')

    def test_requires_login(self):
        response = self.client.post(
            reverse('storage:attach_company'), {'company_id': 1},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_missing_company_shows_error(self):
        root = User.objects.create_superuser(
            username='root3', email='root3@example.com', password='SuperPass123',
        )
        original = root.userprofile.company_id
        self.client.force_login(root)
        response = self.client.post(reverse('storage:attach_company'), {})
        self.assertRedirects(response, reverse('storage:dashboard'))
        root.userprofile.refresh_from_db()
        self.assertEqual(root.userprofile.company_id, original)
