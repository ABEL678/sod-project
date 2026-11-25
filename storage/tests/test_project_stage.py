from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from storage.domain.models import Company, Project
from storage.domain.services import create_project, update_project


User = get_user_model()


class ProjectStageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='stage_admin', password='pass')
        self.company = Company.objects.create(name='Stage')
        self.client.force_login(self.user)

    def _project(self, code, stage=None):
        return create_project(
            company=self.company,
            code=code,
            name=f'Проект {code}',
            customer_id=None,
            created_by=self.user,
            stage=stage,
        )

    def test_create_without_stage_is_empty(self):
        project = self._project('STG-NONE')
        project.refresh_from_db()
        self.assertIsNone(project.stage)

    def test_set_p_and_rd(self):
        p = self._project('STG-P', stage=Project.STAGE_P)
        rd = self._project('STG-RD', stage=Project.STAGE_RD)
        p.refresh_from_db()
        rd.refresh_from_db()
        self.assertEqual(p.stage, 'P')
        self.assertEqual(rd.stage, 'RD')

    def test_invalid_stage_rejected_on_update(self):
        project = self._project('STG-BAD')
        with self.assertRaises(ValueError):
            update_project(self.user, project, stage='XX')
        project.refresh_from_db()
        self.assertIsNone(project.stage)

    def test_update_stage_and_clear(self):
        project = self._project('STG-UPD')
        update_project(self.user, project, stage=Project.STAGE_RD)
        project.refresh_from_db()
        self.assertEqual(project.stage, 'RD')
        update_project(self.user, project, stage='')
        project.refresh_from_db()
        self.assertIsNone(project.stage)

    def test_list_shows_dash_for_empty_stage(self):
        self._project('STG-LIST')
        response = self.client.get(reverse('storage:project_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'STG-LIST')
        self.assertContains(response, '<td>—</td>', html=False)

    def test_list_shows_stage_label(self):
        self._project('STG-SHOW', stage=Project.STAGE_RD)
        response = self.client.get(reverse('storage:project_list'))
        self.assertContains(response, '<td>РД</td>', html=False)
