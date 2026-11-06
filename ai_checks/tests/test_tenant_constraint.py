"""Ф0.2 (часть 2): CheckConstraint ai_internal_doc_tenant_scope (8.2).

Логика (после ужесточения по замечанию N4):
- global  ⇒ company IS NULL AND project IS NULL;
- company ⇒ company NOT NULL AND project IS NULL;
- project ⇒ project NOT NULL (company наследуется от проекта).
"""

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase

from ai_checks.domain.models import InternalDocument
from storage.domain.models import (
    Company,
    Document,
    Project,
    Section,
)


class TenantScopeConstraintTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='scope_tester', password='TestPass123',
        )
        self.company = Company.objects.create(name='Скоуп-Компания')
        self.other_project = Project.objects.create(
            company=self.company,
            code='SCOPE-1',
            name='Проект скоупа',
            status='active',
            created_by=self.user,
        )
        self.section = Section.objects.create(
            project=self.other_project,
            code='АР',
            name='Архитектура',
            order=1,
        )
        self.document = Document.objects.create(
            project=self.other_project,
            section=self.section,
            code='SCOPE-SRC',
            name='Источник',
            doc_type='report',
            status='draft',
            created_by=self.user,
        )

    def _create(self, **overrides):
        data = {
            'tenant_scope': InternalDocument.TenantScope.COMPANY,
            'company': self.company,
            'project': None,
            'source_document': self.document,
            'doc_type': InternalDocument.DocType.OTHER,
            'title': 'Тест скоупа',
            'created_by': self.user,
        }
        data.update(overrides)
        return InternalDocument.objects.create(**data)

    def test_company_scope_without_project_ok(self):
        doc = self._create(
            tenant_scope=InternalDocument.TenantScope.COMPANY,
            company=self.company,
            project=None,
        )
        self.assertIsNotNone(doc.pk)

    def test_company_scope_with_project_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._create(
                    tenant_scope=InternalDocument.TenantScope.COMPANY,
                    company=self.company,
                    project=self.other_project,
                )

    def test_global_scope_without_company_and_project_ok(self):
        doc = self._create(
            tenant_scope=InternalDocument.TenantScope.GLOBAL,
            company=None,
            project=None,
        )
        self.assertIsNotNone(doc.pk)

    def test_global_scope_with_company_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._create(
                    tenant_scope=InternalDocument.TenantScope.GLOBAL,
                    company=self.company,
                    project=None,
                )

    def test_project_scope_without_project_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._create(
                    tenant_scope=InternalDocument.TenantScope.PROJECT,
                    company=self.company,
                    project=None,
                )

    def test_project_scope_with_project_ok(self):
        doc = self._create(
            tenant_scope=InternalDocument.TenantScope.PROJECT,
            company=self.company,
            project=self.other_project,
        )
        self.assertIsNotNone(doc.pk)
