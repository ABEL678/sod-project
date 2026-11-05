import json
import secrets
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from external_api.models import ApiToken
from issues.models import Issue
from storage.domain.models import (
    Company,
    Document,
    Project,
    Section,
    Specification,
    UserProfile,
    VorItem,
)
from storage.domain.services import create_api_token, create_vor_items


User = get_user_model()


class Api4CTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Компания 4C")
        self.admin = User.objects.create_user("api-4c-admin", password="pass")
        UserProfile.objects.create(
            user=self.admin,
            company=self.company,
            role="admin",
        )
        self.project = Project.objects.create(
            company=self.company,
            code="PROJECT-4C",
            name="Проект 4C",
            status="active",
            created_by=self.admin,
        )
        self.section = Section.objects.create(
            project=self.project,
            code="AR",
            name="Архитектурные решения",
            order=1,
        )
        self.document = Document.objects.create(
            project=self.project,
            section=self.section,
            code="DOC-4C-01",
            name="Документ 4C",
            doc_type="drawing",
            status="draft",
            created_by=self.admin,
        )
        self.token, self.raw = create_api_token(
            self.admin,
            self.company,
            "4C full",
            [
                ApiToken.Scope.PROJECTS_READ,
                ApiToken.Scope.PROJECTS_CREATE,
                ApiToken.Scope.ISSUES_SYNC,
                ApiToken.Scope.ISSUES_READ,
            ],
        )
        self.read_token, self.read_raw = create_api_token(
            self.admin,
            self.company,
            "4C read",
            [ApiToken.Scope.PROJECTS_READ],
        )
        self.issues_token, self.issues_raw = create_api_token(
            self.admin,
            self.company,
            "4C issues",
            [ApiToken.Scope.ISSUES_READ],
        )

    def _headers(self, raw):
        return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}

    def _get(self, url, raw):
        return self.client.get(url, **self._headers(raw))

    def _post(self, url, payload, raw):
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            **self._headers(raw),
        )

    def test_specifications_returns_empty_list(self):
        response = self._get(
            f"/api/v1/projects/{self.project.code}/specifications/",
            self.raw,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_specifications_returns_items(self):
        Specification.objects.create(
            project=self.project,
            section_code="AR",
            code="SPEC-1",
            name="Дверь",
            unit="шт",
            quantity=Decimal("2.00"),
            note="Тестовая строка",
        )

        response = self._get(
            f"/api/v1/projects/{self.project.code}/specifications/",
            self.raw,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["code"], "SPEC-1")
        self.assertEqual(payload[0]["section_code"], "AR")
        self.assertEqual(payload[0]["quantity"], "2.00")

    def test_specifications_without_scope_returns_403(self):
        response = self._get(
            f"/api/v1/projects/{self.project.code}/specifications/",
            self.issues_raw,
        )

        self.assertEqual(response.status_code, 403)

    def test_specifications_unknown_project_returns_404(self):
        response = self._get(
            "/api/v1/projects/UNKNOWN-4C/specifications/",
            self.raw,
        )

        self.assertEqual(response.status_code, 404)

    def test_vor_creates_items(self):
        response = self._post(
            f"/api/v1/projects/{self.project.code}/vor/",
            [
                {
                    "code": "VOR-1",
                    "name": "Позиция ВОР",
                    "unit": "шт",
                    "quantity": 2,
                    "section_code": "AR",
                }
            ],
            self.raw,
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["created"], 1)
        item = VorItem.objects.get(project=self.project, code="VOR-1")
        self.assertEqual(item.name, "Позиция ВОР")
        self.assertEqual(item.quantity, Decimal("2.00"))

    def test_vor_invalid_quantity_returns_400(self):
        response = self._post(
            f"/api/v1/projects/{self.project.code}/vor/",
            [{"code": "VOR-ZERO", "name": "Ноль", "unit": "шт", "quantity": 0}],
            self.raw,
        )

        self.assertEqual(response.status_code, 400)

    def test_create_vor_items_rejects_non_admin(self):
        stranger = User.objects.create_user("vor-stranger", password="pass")

        with self.assertRaises(PermissionDenied):
            create_vor_items(
                stranger,
                self.project,
                [{"code": "VOR-X", "name": "X", "unit": "шт", "quantity": 1}],
            )

    def test_vor_without_scope_returns_403(self):
        response = self._post(
            f"/api/v1/projects/{self.project.code}/vor/",
            [{"code": "VOR-1", "name": "Позиция"}],
            self.read_raw,
        )

        self.assertEqual(response.status_code, 403)

    def test_issue_sync_creates_issue(self):
        response = self._post(
            f"/api/v1/projects/{self.project.code}/issues/sync/",
            [
                {
                    "document_code": self.document.code,
                    "issue_text": "Проверить узел",
                    "status": "NEW",
                    "page_number": 1,
                    "x_ratio": 0.1,
                    "y_ratio": 0.2,
                }
            ],
            self.raw,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["synced"], 1)
        issue = Issue.objects.get(document=self.document)
        self.assertEqual(issue.description, "Проверить узел")
        self.assertEqual(issue.page_number, 1)

    def test_issue_sync_external_id_updates_without_duplicate(self):
        url = f"/api/v1/projects/{self.project.code}/issues/sync/"

        first = self._post(
            url,
            [
                {
                    "external_id": "VITRO-1",
                    "document_code": self.document.code,
                    "issue_text": "Исходный текст",
                    "status": "NEW",
                }
            ],
            self.raw,
        )
        self.assertEqual(first.status_code, 200)

        second = self._post(
            url,
            [
                {
                    "external_id": "VITRO-1",
                    "document_code": self.document.code,
                    "issue_text": "Изменённый текст",
                    "status": "IN_PROGRESS",
                }
            ],
            self.raw,
        )

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["synced"], 1)
        self.assertEqual(
            Issue.objects.filter(external_id="VITRO-1").count(), 1
        )
        issue = Issue.objects.get(external_id="VITRO-1")
        self.assertEqual(issue.description, "Изменённый текст")
        self.assertEqual(issue.status, "IN_PROGRESS")

    def test_issue_sync_different_external_ids_same_text_creates_two(self):
        url = f"/api/v1/projects/{self.project.code}/issues/sync/"

        for external_id in ("VITRO-A", "VITRO-B"):
            response = self._post(
                url,
                [
                    {
                        "external_id": external_id,
                        "document_code": self.document.code,
                        "issue_text": "Одинаковый текст",
                        "status": "NEW",
                    }
                ],
                self.raw,
            )
            self.assertEqual(response.status_code, 200)

        self.assertEqual(
            Issue.objects.filter(
                document=self.document,
                description="Одинаковый текст",
            ).count(),
            2,
        )

    def test_issue_sync_without_external_id_uses_text_fallback(self):
        url = f"/api/v1/projects/{self.project.code}/issues/sync/"
        payload = [
            {
                "document_code": self.document.code,
                "issue_text": "Fallback текст",
                "status": "NEW",
            }
        ]

        first = self._post(url, payload, self.raw)
        second = self._post(url, payload, self.raw)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            Issue.objects.filter(
                document=self.document,
                description="Fallback текст",
            ).count(),
            1,
        )

    def test_issue_sync_without_scope_returns_403(self):
        response = self._post(
            f"/api/v1/projects/{self.project.code}/issues/sync/",
            [{"document_code": self.document.code, "issue_text": "Текст"}],
            self.read_raw,
        )

        self.assertEqual(response.status_code, 403)

    def test_issue_pending_returns_list(self):
        Issue.objects.create(
            document=self.document,
            author=self.admin,
            description="Ожидает",
            status="NEW",
            external_id="VITRO-PENDING",
        )

        response = self._get(
            f"/api/v1/projects/{self.project.code}/issues/pending/",
            self.raw,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["document_code"], self.document.code)
        self.assertEqual(payload[0]["text"], "Ожидает")
        self.assertEqual(payload[0]["external_id"], "VITRO-PENDING")

    def test_issue_pending_without_scope_returns_403(self):
        response = self._get(
            f"/api/v1/projects/{self.project.code}/issues/pending/",
            self.read_raw,
        )

        self.assertEqual(response.status_code, 403)

    def test_issue_pending_unknown_project_returns_404(self):
        response = self._get(
            "/api/v1/projects/UNKNOWN-4C/issues/pending/",
            self.raw,
        )

        self.assertEqual(response.status_code, 404)
