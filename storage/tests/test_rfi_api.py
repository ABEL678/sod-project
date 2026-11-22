"""Тесты API для заданий и RFI (Этап R3, внешний API ГИП-Ассистента).

Покрытие: 401 без токена, 403 без scope, 404 чужой проект,
состав JSON для tasks и rfis, фильтр по статусу.
"""

import hashlib
import json

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from external_api.models import ApiToken
from storage.domain.models import (
    Company,
    DesignTask,
    Folder,
    Project,
    ProjectMembership,
    RfiTask,
    Section,
    User,
    UserProfile,
)


def _auth_header(token_raw):
    return {'HTTP_AUTHORIZATION': f'Bearer {token_raw}'}


_token_counter = 0


def _make_token(company, user, scopes=None):
    global _token_counter
    _token_counter += 1
    raw = f'test-token-{_token_counter}-{company.pk}'
    prefix = raw[:8]
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    token = ApiToken.objects.create(
        company=company, name='Test', token_prefix=prefix,
        token_hash=token_hash, scopes=scopes or ['tasks:read'],
        created_by=user,
    )
    return raw, token


class RfiApiTestBase(TestCase):
    """Фикстуры: компания, проект, пользователи, токен."""

    def setUp(self):
        self.company = Company.objects.create(name='API Company')
        self.other_company = Company.objects.create(name='Other Co')

        self.admin = User.objects.create_user('api_admin', password='pass')
        UserProfile.objects.update_or_create(
            user=self.admin, defaults={'role': 'admin', 'company': self.company},
        )
        self.gip = User.objects.create_user('api_gip', password='pass')
        UserProfile.objects.update_or_create(
            user=self.gip, defaults={'role': 'gip', 'company': self.company},
        )

        self.project = Project.objects.create(
            company=self.company, code='API-TEST', name='API Project',
            status='active', created_by=self.admin,
        )
        ProjectMembership.objects.update_or_create(project=self.project, user=self.gip, defaults={'role_in_project': 'gip'})
        self.other_project = Project.objects.create(
            company=self.other_company, code='OTHER', name='Other',
            status='active', created_by=self.admin,
        )

        self.section_ar = Section.objects.create(
            project=self.project, code='AR', name='АР', order=1,
        )
        self.section_kr = Section.objects.create(
            project=self.project, code='KR', name='КР', order=2,
        )
        self.common = Section.objects.create(
            project=self.project, code='COMMON', name='COMMON', order=0,
        )

        self.raw_token, self.token = _make_token(self.company, self.admin)
        self.other_raw, self.other_token = _make_token(
            self.other_company, self.admin,
        )
        self.no_scope_raw, self.no_scope_token = _make_token(
            self.company, self.admin, scopes=['projects:read'],
        )

    def _make_task(self, **kw):
        defaults = dict(
            project=self.project, title='Test task',
            description='Test', source_section=self.section_ar,
            target_section=self.section_kr, author=self.admin,
            assignee=self.gip, status=DesignTask.Status.ISSUED,
        )
        defaults.update(kw)
        return DesignTask.objects.create(**defaults)

    def _make_rfi(self, **kw):
        defaults = dict(
            project=self.project, number=1, subject='Test RFI',
            reason='Need data', author=self.admin, assignee=self.gip,
            status='open',
        )
        defaults.update(kw)
        return RfiTask.objects.create(**defaults)


class ApiTaskListViewTests(RfiApiTestBase):
    """GET /api/v1/projects/<code>/tasks/."""

    def _url(self, code=None):
        return reverse(
            'api_project_tasks',
            kwargs={'code': code or self.project.code},
        )

    def test_401_without_token(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 401)

    def test_403_without_scope(self):
        resp = self.client.get(
            self._url(), **_auth_header(self.no_scope_raw),
        )
        self.assertEqual(resp.status_code, 403)

    def test_404_other_company_project(self):
        resp = self.client.get(
            self._url(code=self.other_project.code),
            **_auth_header(self.raw_token),
        )
        self.assertEqual(resp.status_code, 404)

    def test_returns_empty_list(self):
        resp = self.client.get(
            self._url(), **_auth_header(self.raw_token),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['count'], 0)
        self.assertEqual(data['tasks'], [])

    def test_returns_task_data(self):
        task = self._make_task()
        resp = self.client.get(
            self._url(), **_auth_header(self.raw_token),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['count'], 1)
        t = data['tasks'][0]
        self.assertEqual(t['id'], task.pk)
        self.assertEqual(t['title'], 'Test task')
        self.assertEqual(t['ga_status'], 'NOT_STARTED')
        self.assertIn('project', data)
        self.assertIn('summary', data)

    def test_summary_fields(self):
        self._make_task()
        resp = self.client.get(
            self._url(), **_auth_header(self.raw_token),
        )
        data = resp.json()
        s = data['summary']
        for key in ('total', 'overdue', 'on_hold', 'awaiting_review', 'by_status'):
            self.assertIn(key, s)

    def test_ga_status_mapping(self):
        """Проверяем маппинг статусов DesignTask → ga_status."""
        task = self._make_task(status=DesignTask.Status.IN_PROGRESS)
        resp = self.client.get(
            self._url(), **_auth_header(self.raw_token),
        )
        self.assertEqual(resp.json()['tasks'][0]['ga_status'], 'IN_PROGRESS')


class ApiRfiListViewTests(RfiApiTestBase):
    """GET /api/v1/projects/<code>/rfis/."""

    def _url(self, code=None):
        return reverse(
            'api_project_rfis',
            kwargs={'code': code or self.project.code},
        )

    def test_401_without_token(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 401)

    def test_403_without_scope(self):
        resp = self.client.get(
            self._url(), **_auth_header(self.no_scope_raw),
        )
        self.assertEqual(resp.status_code, 403)

    def test_404_other_company_project(self):
        resp = self.client.get(
            self._url(code=self.other_project.code),
            **_auth_header(self.raw_token),
        )
        self.assertEqual(resp.status_code, 404)

    def test_returns_empty_list(self):
        resp = self.client.get(
            self._url(), **_auth_header(self.raw_token),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['count'], 0)
        self.assertEqual(data['rfis'], [])

    def test_returns_rfi_data(self):
        rfi = self._make_rfi()
        resp = self.client.get(
            self._url(), **_auth_header(self.raw_token),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['count'], 1)
        r = data['rfis'][0]
        self.assertEqual(r['id'], rfi.pk)
        self.assertEqual(r['title'], 'Test RFI')
        self.assertEqual(r['ga_rfi_state'], 'open')
        self.assertEqual(r['number_display'], 'RFI-01')
        self.assertIn('project', data)
        self.assertIn('summary', data)

    def test_filter_by_status(self):
        self._make_rfi(number=1, status='open')
        self._make_rfi(number=2, subject='Sent', status='sent')
        resp = self.client.get(
            self._url() + '?status=sent', **_auth_header(self.raw_token),
        )
        data = resp.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['rfis'][0]['status'], 'sent')

    def test_filter_by_attention(self):
        self._make_rfi(number=1, status='open')
        rfi = self._make_rfi(number=2, subject='Esc', status='escalated')
        RfiTask.objects.filter(pk=rfi.pk).update(
            rfi_due_date=timezone.localdate(),
        )
        resp = self.client.get(
            self._url() + '?attention=1', **_auth_header(self.raw_token),
        )
        data = resp.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['rfis'][0]['status'], 'escalated')

    def test_summary_fields(self):
        self._make_rfi()
        resp = self.client.get(
            self._url(), **_auth_header(self.raw_token),
        )
        data = resp.json()
        s = data['summary']
        for key in ('total', 'open', 'sent', 'answered', 'registered', 'closed', 'escalated', 'overdue'):
            self.assertIn(key, s)