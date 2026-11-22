import hashlib
import json
import secrets

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from external_api.models import ApiToken
from storage.domain.models import Company, Customer, UserProfile
from storage.domain.services import (
    create_customer,
    create_project,
    resolve_customer_name,
    update_project,
)


User = get_user_model()


class CustomerCardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='cust_admin', password='pass')
        self.company = Company.objects.create(name='Тенант')
        self.other = Company.objects.create(name='Другой')
        UserProfile.objects.update_or_create(
            user=self.user, defaults={'role': 'admin', 'company': self.company},
        )
        self.client.force_login(self.user)

    def _project(self, code, customer=None):
        return create_project(
            company=self.company,
            code=code,
            name=f'Проект {code}',
            customer_id=customer.pk if customer else None,
            created_by=self.user,
        )

    def _token(self, scopes):
        raw = secrets.token_urlsafe(48)
        ApiToken.objects.create(
            company=self.company,
            name='Token customer',
            token_prefix=raw[:8],
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            scopes=scopes,
            created_by=self.user,
        )
        return raw

    # --- карточка и дедупликация (сервис) ---

    def test_create_customer_card(self):
        customer = create_customer(self.user, self.company, '  ООО Ромашка  ')
        self.assertEqual(customer.name, 'ООО Ромашка')
        self.assertEqual(customer.company, self.company)

    def test_exact_duplicate_rejected(self):
        create_customer(self.user, self.company, 'ООО Ромашка')
        with self.assertRaises(ValueError):
            create_customer(self.user, self.company, 'ООО Ромашка')
        self.assertEqual(Customer.objects.filter(company=self.company).count(), 1)

    def test_different_spelling_not_merged(self):
        create_customer(self.user, self.company, 'ООО Ромашка')
        create_customer(self.user, self.company, 'ООО "Ромашка"')
        self.assertEqual(Customer.objects.filter(company=self.company).count(), 2)

    def test_same_name_allowed_in_other_company(self):
        create_customer(self.user, self.company, 'ООО Ромашка')
        other_user = User.objects.create_superuser(username='cust_other', password='pass')
        customer = create_customer(other_user, self.other, 'ООО Ромашка')
        self.assertEqual(customer.company, self.other)

    def test_empty_name_rejected(self):
        with self.assertRaises(ValueError):
            create_customer(self.user, self.company, '   ')

    def test_foreign_company_rejected_for_non_superuser(self):
        outsider = User.objects.create_user(username='cust_outsider', password='pass')
        with self.assertRaises(PermissionDenied):
            create_customer(outsider, self.company, 'Чужая организация')

    # --- привязка по id ---

    def test_create_project_by_customer_id(self):
        card = create_customer(self.user, self.company, 'АО Заказчик')
        project = self._project('CST-ID', customer=card)
        project.refresh_from_db()
        self.assertEqual(project.customer_org, card)
        self.assertEqual(project.customer, 'АО Заказчик')

    def test_create_project_rejects_customer_from_other_company(self):
        foreign = create_customer(self.user, self.other, 'Чужой заказчик')
        with self.assertRaises(ValueError):
            self._project('CST-FOREIGN', customer=foreign)

    def test_update_project_relinks_by_id(self):
        first = create_customer(self.user, self.company, 'Первый')
        second = create_customer(self.user, self.company, 'Второй')
        project = self._project('CST-RELINK', customer=first)
        update_project(self.user, project, customer_id=second.pk)
        project.refresh_from_db()
        self.assertEqual(project.customer_org, second)

    def test_update_project_clears_customer_with_none(self):
        card = create_customer(self.user, self.company, 'Снять')
        project = self._project('CST-CLEAR', customer=card)
        update_project(self.user, project, customer_id=None)
        project.refresh_from_db()
        self.assertIsNone(project.customer_org)

    def test_update_project_without_customer_keeps_card(self):
        card = create_customer(self.user, self.company, 'Оставить')
        project = self._project('CST-KEEP', customer=card)
        update_project(self.user, project, name='Новое имя')
        project.refresh_from_db()
        self.assertEqual(project.customer_org, card)

    def test_empty_customer_leaves_no_card(self):
        project = self._project('CST-EMPTY')
        project.refresh_from_db()
        self.assertIsNone(project.customer_org)
        self.assertEqual(project.customer, '')

    # --- UI ---

    def test_customer_list_page_shows_card(self):
        create_customer(self.user, self.company, 'Карточка Тест')
        response = self.client.get(reverse('storage:customer_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Карточка Тест')

    def test_project_list_offers_customer_select_by_id(self):
        card = create_customer(self.user, self.company, 'Выбор Заказчик')
        self._project('CST-SEL')
        response = self.client.get(reverse('storage:project_list'))
        self.assertContains(response, 'name="customer_id"')
        self.assertContains(response, f'value="{card.pk}"')
        self.assertNotContains(response, 'name="customer" ')

    # --- API ---

    def test_api_name_resolves_to_card(self):
        raw = self._token([ApiToken.Scope.PROJECTS_CREATE])
        self.client.logout()
        response = self.client.post(
            '/api/v1/projects/',
            data=json.dumps({'code': 'API-CARD', 'name': 'API карточка', 'customer': 'Карточка API'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {raw}',
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertNotIn('customer', body)
        self.assertEqual(body['customer_org']['name'], 'Карточка API')
        self.assertTrue(Customer.objects.filter(company=self.company, name='Карточка API').exists())

    def test_api_same_name_reuses_card(self):
        card = create_customer(self.user, self.company, 'Повтор API')
        raw = self._token([ApiToken.Scope.PROJECTS_CREATE])
        self.client.logout()
        response = self.client.post(
            '/api/v1/projects/',
            data=json.dumps({'code': 'API-REUSE', 'name': 'Повтор', 'customer': 'Повтор API'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {raw}',
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['customer_org']['id'], card.pk)
        self.assertEqual(Customer.objects.filter(company=self.company, name='Повтор API').count(), 1)

    def test_api_response_has_no_customer_string(self):
        raw = self._token([ApiToken.Scope.PROJECTS_CREATE])
        self.client.logout()
        response = self.client.post(
            '/api/v1/projects/',
            data=json.dumps({'code': 'API-NOSTR', 'name': 'Без строки'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {raw}',
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn('customer', response.json())

    def test_resolve_customer_name_helper(self):
        card = resolve_customer_name(self.company, '  Маршрут  ')
        self.assertEqual(card.name, 'Маршрут')
        self.assertIsNone(resolve_customer_name(self.company, '   '))
