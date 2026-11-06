import hashlib
import json
import secrets

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from rest_framework.throttling import UserRateThrottle

from external_api.models import ApiToken
from storage.domain.models import Company, Project, UserProfile
from storage.domain.services import (
    create_api_token,
    list_api_tokens,
    revoke_api_token,
)


User = get_user_model()


class ApiTokenServiceTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Компания A")
        self.admin = User.objects.create_user("admin-a", password="pass")
        UserProfile.objects.update_or_create(user=self.admin, defaults={'role': "admin"
        , 'company': self.company})[0]

    def test_create_api_token_returns_raw_and_stores_only_hash(self):
        token, raw = create_api_token(
            self.admin,
            self.company,
            "ГИП-Ассистент",
            [ApiToken.Scope.PROJECTS_CREATE],
        )

        self.assertTrue(raw)
        self.assertGreaterEqual(len(raw), 64)
        self.assertEqual(token.token_prefix, raw[:8])
        self.assertEqual(
            token.token_hash, hashlib.sha256(raw.encode()).hexdigest()
        )
        self.assertNotEqual(token.token_hash, raw)
        self.assertEqual(token.company, self.company)
        self.assertEqual(token.created_by, self.admin)
        self.assertEqual(
            token.scopes, [ApiToken.Scope.PROJECTS_CREATE]
        )

    def test_create_api_token_rejects_invalid_scope(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            create_api_token(
                self.admin,
                self.company,
                "Плохой токен",
                ["unknown:scope"],
            )

    def test_create_api_token_rejects_past_expiry(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            create_api_token(
                self.admin,
                self.company,
                "Просроченный",
                [ApiToken.Scope.PROJECTS_CREATE],
                expires_at=timezone.now() - timezone.timedelta(days=1),
            )

    def test_revoke_api_token_sets_inactive(self):
        token, _raw = create_api_token(
            self.admin,
            self.company,
            "Для отзыва",
            [ApiToken.Scope.PROJECTS_CREATE],
        )

        revoke_api_token(self.admin, token)
        token.refresh_from_db()

        self.assertFalse(token.is_active)

    def test_list_api_tokens_scoped_to_company(self):
        other_company = Company.objects.create(name="Компания B")
        other_admin = User.objects.create_user("admin-b", password="pass")
        UserProfile.objects.update_or_create(user=other_admin, defaults={'role': "admin"
        , 'company': other_company})[0]

        own_token, _ = create_api_token(
            self.admin,
            self.company,
            "Свой",
            [ApiToken.Scope.PROJECTS_CREATE],
        )
        foreign_token, _ = create_api_token(
            other_admin,
            other_company,
            "Чужой",
            [ApiToken.Scope.PROJECTS_CREATE],
        )

        tokens = list(list_api_tokens(self.admin, self.company))

        self.assertIn(own_token, tokens)
        self.assertNotIn(foreign_token, tokens)


class ProjectsApiTests(TestCase):
    def setUp(self):
        self.company_a = Company.objects.create(name="Компания A")
        self.company_b = Company.objects.create(name="Компания B")

        self.admin_a = User.objects.create_user("admin-a", password="pass")
        UserProfile.objects.update_or_create(user=self.admin_a, defaults={'role': "admin"
        , 'company': self.company_a})[0]
        self.admin_b = User.objects.create_user("admin-b", password="pass")
        UserProfile.objects.update_or_create(user=self.admin_b, defaults={'role': "admin"
        , 'company': self.company_b})[0]

        self.url = "/api/v1/projects/"

    def _make_token(self, company, scopes, raw=None, expires_at=None):
        raw = raw or secrets.token_urlsafe(48)
        token = ApiToken.objects.create(
            company=company,
            name=f"Token {company.name}",
            token_prefix=raw[:8],
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            scopes=scopes,
            expires_at=expires_at,
            created_by=(
                self.admin_a if company == self.company_a else self.admin_b
            ),
        )
        return token, raw

    def _post(self, payload, raw=None):
        headers = {}
        if raw is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {raw}"
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )

    def test_post_without_token_returns_401(self):
        response = self._post({"code": "TEST-01", "name": "Без токена"})

        self.assertEqual(response.status_code, 401)
        self.assertIn("errors", response.json())

    def test_post_with_invalid_token_returns_401(self):
        response = self._post(
            {"code": "TEST-02", "name": "Невалидный"},
            raw="not-a-real-token",
        )

        self.assertEqual(response.status_code, 401)

    def test_post_with_expired_token_returns_401(self):
        _token, raw = self._make_token(
            self.company_a,
            [ApiToken.Scope.PROJECTS_CREATE],
            expires_at=timezone.now() - timezone.timedelta(minutes=1),
        )

        response = self._post(
            {"code": "TEST-03", "name": "Просроченный"},
            raw=raw,
        )

        self.assertEqual(response.status_code, 401)

    def test_post_without_required_scope_returns_403(self):
        _token, raw = self._make_token(
            self.company_a,
            [ApiToken.Scope.PROJECTS_READ],
        )

        response = self._post(
            {"code": "TEST-04", "name": "Без scope"},
            raw=raw,
        )

        self.assertEqual(response.status_code, 403)

    def test_post_with_valid_token_creates_project_in_token_company(self):
        _token, raw = self._make_token(
            self.company_a,
            [ApiToken.Scope.PROJECTS_CREATE],
        )

        response = self._post(
            {
                "code": "TEST-API-01",
                "name": "Тест API",
                "customer": "ООО Тест",
                "company": self.company_b.pk,
            },
            raw=raw,
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["code"], "TEST-API-01")

        project = Project.objects.get(code="TEST-API-01")
        self.assertEqual(project.company, self.company_a)
        self.assertEqual(project.name, "Тест API")
        self.assertEqual(project.customer, "ООО Тест")
        self.assertFalse(
            Project.objects.filter(company=self.company_b).exists()
        )

    def test_duplicate_code_returns_400(self):
        Project.objects.create(
            company=self.company_a,
            code="TEST-DUP",
            name="Уже есть",
            status="active",
            created_by=self.admin_a,
        )
        _token, raw = self._make_token(
            self.company_a,
            [ApiToken.Scope.PROJECTS_CREATE],
        )

        response = self._post(
            {"code": "TEST-DUP", "name": "Дубликат"},
            raw=raw,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Project.objects.filter(code="TEST-DUP").count(), 1)

    def test_revoked_token_returns_401(self):
        token, raw = self._make_token(
            self.company_a,
            [ApiToken.Scope.PROJECTS_CREATE],
        )
        revoke_api_token(self.admin_a, token)

        response = self._post(
            {"code": "TEST-REVOKED", "name": "Отозванный"},
            raw=raw,
        )

        self.assertEqual(response.status_code, 401)

    @override_settings(
        REST_FRAMEWORK={
            'DEFAULT_THROTTLE_RATES': {
                'user': '2/min',
                'anon': '2/min',
            },
        }
    )
    def test_project_create_is_throttled(self):
        old_rates = UserRateThrottle.THROTTLE_RATES
        try:
            with override_settings(
                REST_FRAMEWORK={
                    'DEFAULT_THROTTLE_RATES': {
                        'user': '2/min',
                        'anon': '2/min',
                    },
                }
            ):
                # DRF кэширует ставки в class attribute при импорте.
                UserRateThrottle.THROTTLE_RATES = {
                    'user': '2/min',
                    'anon': '2/min',
                }
                cache.clear()
                _token, raw = self._make_token(
                    self.company_a,
                    [ApiToken.Scope.PROJECTS_CREATE],
                )

                for index in range(2):
                    response = self._post(
                        {
                            "code": f"THROTTLE-{index}",
                            "name": f"Throttle {index}",
                        },
                        raw=raw,
                    )
                    self.assertEqual(response.status_code, 201)

                response = self._post(
                    {"code": "THROTTLE-3", "name": "Throttle 3"},
                    raw=raw,
                )
                self.assertEqual(response.status_code, 429)
        finally:
            UserRateThrottle.THROTTLE_RATES = old_rates
            cache.clear()

    def test_authentication_updates_last_used_at(self):
        token, raw = self._make_token(
            self.company_a,
            [ApiToken.Scope.PROJECTS_CREATE],
        )
        self.assertIsNone(token.last_used_at)

        response = self._post(
            {"code": "TEST-USED", "name": "Использованный"},
            raw=raw,
        )

        self.assertEqual(response.status_code, 201)
        token.refresh_from_db()
        self.assertIsNotNone(token.last_used_at)


class ApiTokenUiTests(TestCase):
    def setUp(self):
        self.company_a = Company.objects.create(name="Компания A")
        self.company_b = Company.objects.create(name="Компания B")

        self.admin_a = User.objects.create_user("admin-a", password="pass")
        UserProfile.objects.update_or_create(user=self.admin_a, defaults={'role': "admin"
        , 'company': self.company_a})[0]
        self.admin_b = User.objects.create_user("admin-b", password="pass")
        UserProfile.objects.update_or_create(user=self.admin_b, defaults={'role': "admin"
        , 'company': self.company_b})[0]
        self.gip_a = User.objects.create_user("gip-a", password="pass")
        UserProfile.objects.update_or_create(user=self.gip_a, defaults={'role': "gip"
        , 'company': self.company_a})[0]

    def test_admin_sees_only_own_company_tokens(self):
        own_token, _ = create_api_token(
            self.admin_a,
            self.company_a,
            "Свой",
            [ApiToken.Scope.PROJECTS_CREATE],
        )
        foreign_token, _ = create_api_token(
            self.admin_b,
            self.company_b,
            "Чужой",
            [ApiToken.Scope.PROJECTS_CREATE],
        )
        self.client.force_login(self.admin_a)

        response = self.client.get(reverse("external_api:api_token_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, own_token.name)
        self.assertNotContains(response, foreign_token.name)

    def test_non_admin_cannot_open_tokens_page(self):
        self.client.force_login(self.gip_a)

        response = self.client.get(reverse("external_api:api_token_list"))

        self.assertEqual(response.status_code, 403)

    def test_create_token_shows_raw_once(self):
        self.client.force_login(self.admin_a)

        response = self.client.post(
            reverse("external_api:api_token_create"),
            {
                "name": "ГИП-Ассистент",
                "scopes": [ApiToken.Scope.PROJECTS_CREATE],
            },
        )

        self.assertEqual(response.status_code, 302)
        token = ApiToken.objects.get(name="ГИП-Ассистент")
        self.assertNotIn("api_token_raw_once", self.client.session)
        self.assertIn("api_token_raw_cache_key", self.client.session)

        response = self.client.get(reverse("external_api:api_token_list"))
        self.assertEqual(response.status_code, 200)
        shown_token = response.context["created_token"]["token"]
        self.assertIn("no-store", response["Cache-Control"])

        self.assertIn(shown_token, response.content.decode())
        self.assertEqual(
            hashlib.sha256(shown_token.encode()).hexdigest(),
            token.token_hash,
        )

        response = self.client.get(reverse("external_api:api_token_list"))
        self.assertNotIn(shown_token, response.content.decode())

    @override_settings(
        CACHES={
            'default': {
                'BACKEND': (
                    'django.core.cache.backends.dummy.DummyCache'
                ),
            },
        }
    )
    def test_create_token_returns_raw_directly_without_cache(self):
        self.client.force_login(self.admin_a)

        response = self.client.post(
            reverse("external_api:api_token_create"),
            {
                "name": "Без кэша",
                "scopes": [ApiToken.Scope.PROJECTS_CREATE],
            },
        )

        self.assertEqual(response.status_code, 200)
        created = response.context["created_token"]
        self.assertIsNotNone(created)
        self.assertIn(created["token"], response.content.decode())
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn("api_token_raw_cache_key", self.client.session)

    def test_revoke_token_from_ui(self):
        token, _ = create_api_token(
            self.admin_a,
            self.company_a,
            "Для UI",
            [ApiToken.Scope.PROJECTS_CREATE],
        )
        self.client.force_login(self.admin_a)

        response = self.client.post(
            reverse("external_api:api_token_revoke", args=[token.pk])
        )

        self.assertEqual(response.status_code, 302)
        token.refresh_from_db()
        self.assertFalse(token.is_active)
