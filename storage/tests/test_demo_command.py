import shutil
import tempfile
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from issues.models import Issue
from storage.domain.models import (
    Company,
    Document,
    DocumentVersion,
    Project,
    UserProfile,
)
from storage.management.commands.create_demo_project import (
    DEMO_CODE,
    DEMO_PASSWORD,
)


User = get_user_model()


class CreateDemoProjectTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)

    def _run(self, **options):
        stdout = StringIO()
        # Спринт 7: тестовый раннер принудительно ставит DEBUG=False,
        # поэтому все вызовы проходят с force=True (guard проверяется
        # отдельно в test_sprint7.DemoCommandGuardTests).
        options.setdefault('force', True)
        call_command(
            'create_demo_project',
            stdout=stdout,
            stderr=StringIO(),
            **options,
        )
        return stdout.getvalue()

    def test_create_demo_project_fills_data_and_public_link(self):
        output = self._run()

        project = Project.objects.get(code=DEMO_CODE)
        self.assertEqual(project.name, 'Демо-проект ЖК Солнечный')
        self.assertEqual(project.template_key, 'FULL')
        self.assertEqual(Document.objects.filter(project=project).count(), 4)
        self.assertEqual(
            DocumentVersion.objects.filter(document__project=project).count(),
            5,
        )
        self.assertEqual(Issue.objects.filter(document__project=project).count(), 6)

        plan = Document.objects.get(
            project=project,
            code='2345-12-АР-01',
        )
        self.assertEqual(plan.versions.count(), 2)
        self.assertIsNotNone(plan.public_link_token)
        self.assertIn('Демо-проект создан', output)
        self.assertIn('Share-ссылка:', output)
        for username in (
            'demo_admin',
            'demo_checker',
            'demo_developer',
            'demo_gip',
        ):
            self.assertIn(f'{username} / {DEMO_PASSWORD}', output)

        self.assertTrue(
            self.client.login(username='demo_admin', password=DEMO_PASSWORD)
        )
        response = self.client.get(
            reverse('storage:project_detail', args=[project.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Демо-проект ЖК Солнечный')
        self.assertContains(response, 'План 1 этажа')

        share_url = (
            reverse('storage:document_detail', args=[plan.pk])
            + f'?share={plan.public_link_token}'
        )
        public_response = Client().get(share_url)
        self.assertEqual(public_response.status_code, 200)
        self.assertContains(public_response, 'План 1 этажа')
        self.assertNotContains(
            public_response,
            'Проверить размеры помещения на плане 1 этажа.',
        )

    def test_second_run_without_reset_fails(self):
        self._run()

        with self.assertRaises(CommandError) as error:
            self._run()

        self.assertIn('--reset', str(error.exception))
        self.assertEqual(Project.objects.filter(code=DEMO_CODE).count(), 1)

    def test_reset_recreates_project(self):
        self._run()
        old_pk = Project.objects.get(code=DEMO_CODE).pk

        self._run(reset=True)

        project = Project.objects.get(code=DEMO_CODE)
        self.assertNotEqual(project.pk, old_pk)
        self.assertFalse(Project.objects.filter(pk=old_pk).exists())
        self.assertEqual(Document.objects.filter(project=project).count(), 4)
        self.assertEqual(Issue.objects.filter(document__project=project).count(), 6)

    def test_company_id_and_user_username_are_used(self):
        company = Company.objects.create(name='Тестовая компания')
        admin = User.objects.create_user('owner_admin', password='secret')
        UserProfile.objects.create(user=admin, company=company, role='admin')

        self._run(company_id=company.pk, user_username='owner_admin')

        project = Project.objects.get(code=DEMO_CODE)
        self.assertEqual(project.company_id, company.pk)
        self.assertEqual(project.created_by_id, admin.pk)
