"""Вьюха и шаблон «Матрицы заданий» (этап M3).

Покрывает план §4.2 (вкладка ?tab=matrix) и §6.3 (вьюхи): 200 для ГИПа,
откат на documents для не-ГИПа (якорь скрыт, данные не утекают), 404 вне
доступных проектов, решётка из осей без COMMON, пустой проект без
ошибок. Гейт прав — во вьюхе (can_view_all_tasks), сервис прав не проверяет.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from storage.domain.models import (
    Company,
    DesignTask,
    Project,
    ProjectMembership,
    Section,
    UserProfile,
)

User = get_user_model()


class TasksMatrixViewTests(TestCase):
    """Фикстуры: ГИП, разработчик-участник, чужак; оси АР, КР + COMMON."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания Матрица M3')
        self.other_company = Company.objects.create(name='Чужая компания M3')

        self.gip = User.objects.create_user('gip_matrix_m3', password='pass')
        self._profile(self.gip, 'gip', self.company)

        self.developer = User.objects.create_user('dev_matrix_m3', password='pass')
        self._profile(self.developer, 'developer', self.company)

        self.outsider = User.objects.create_user('outsider_matrix_m3', password='pass')
        self._profile(self.outsider, 'developer', self.other_company)

        self.project = Project.objects.create(
            company=self.company,
            code='MX-M3',
            name='Проект матрицы M3',
            status='active',
            created_by=self.gip,
            gip=self.gip,
        )
        self.section_ar = Section.objects.create(
            project=self.project, code='AR', name='Архитектура', order=1,
        )
        self.section_kr = Section.objects.create(
            project=self.project, code='KR', name='Конструкции', order=2,
        )
        Section.objects.create(
            project=self.project, code='COMMON', name='Общие', order=99,
        )
        ProjectMembership.objects.create(
            project=self.project, user=self.gip, role_in_project='gip',
        )
        ProjectMembership.objects.create(
            project=self.project, user=self.developer, role_in_project='developer',
        )

        self.url = reverse('storage:project_detail', args=[self.project.pk])

    def _profile(self, user, role, company):
        UserProfile.objects.update_or_create(
            user=user, defaults={'role': role, 'company': company},
        )

    def _task(self, status, source=None, target=None, due_date=None):
        return DesignTask.objects.create(
            project=self.project,
            source_section=source or self.section_ar,
            target_section=target or self.section_kr,
            author=self.gip,
            title='Задание матрицы',
            description='Описание',
            status=status,
            due_date=due_date,
        )

    def _get_matrix(self, user):
        self.client.force_login(user)
        return self.client.get(self.url, {'tab': 'matrix'})

    # ─── Доступ (§6.3, §4.2 п.6) ─────────────────────────────────────────────

    def test_matrix_tab_ok_for_gip(self):
        response = self._get_matrix(self.gip)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_tab'], 'matrix')
        self.assertTrue(response.context['can_view_matrix'])
        self.assertContains(response, 'data-role="tasks-matrix"')

    def test_matrix_tab_falls_back_to_documents_for_developer(self):
        self._task(DesignTask.Status.IN_PROGRESS)
        response = self._get_matrix(self.developer)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_tab'], 'documents')
        self.assertFalse(response.context['can_view_matrix'])
        self.assertNotContains(response, 'data-role="tasks-matrix"')
        # Якорь вкладки скрыт от не-ГИПа. Проверяем сам <a role="tab">:
        # подстрока ?tab=matrix может встречаться в ссылках панели документов,
        # которая сохраняет GET-параметр tab из URL (не якорь вкладки).
        self.assertNotContains(response, 'href="/projects/%d/?tab=matrix"' % self.project.pk)
        self.assertNotIn('bi-grid-3x3', response.content.decode('utf-8'))

    def test_matrix_anchor_visible_for_gip(self):
        self.client.force_login(self.gip)
        response = self.client.get(self.url, {'tab': 'tasks'})
        self.assertContains(response, 'href="%s?tab=matrix"' % self.url)
        self.assertContains(response, 'bi-grid-3x3')

    def test_project_outside_access_returns_404(self):
        project_other = Project.objects.create(
            company=self.other_company,
            code='OTH-M3',
            name='Чужой проект',
            status='active',
            created_by=self.outsider,
            gip=self.outsider,
        )
        self.client.force_login(self.gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[project_other.pk]),
            {'tab': 'matrix'},
        )
        self.assertEqual(response.status_code, 404)

    # ─── Решётка и контекст (§4.5, §3.2, К3) ─────────────────────────────────

    def test_grid_axes_exclude_common_and_keep_order(self):
        response = self._get_matrix(self.gip)
        codes = [s.code for s in response.context['matrix_sections']]
        self.assertEqual(codes, ['AR', 'KR'])
        self.assertNotContains(response, 'COMMON')

    def test_grid_is_full_square_with_empty_pairs_as_none(self):
        self._task(DesignTask.Status.DRAFT)
        response = self._get_matrix(self.gip)
        grid = response.context['matrix_grid']
        self.assertEqual(len(grid), 2)
        for row in grid:
            self.assertEqual(len(row['cells']), 2)
        ar_kr = grid[0]['cells'][1]
        kr_ar = grid[1]['cells'][0]
        self.assertIsNotNone(ar_kr['cell'])
        self.assertIsNone(kr_ar['cell'])

    def test_cell_shows_status_label_and_four_counters_only(self):
        self._task(DesignTask.Status.IN_PROGRESS)
        self._task(DesignTask.Status.COMPLETED)
        response = self._get_matrix(self.gip)
        cell = response.context['matrix_grid'][0]['cells'][1]['cell']
        self.assertEqual(cell['status'], 'IN_PROGRESS')
        self.assertEqual(cell['label'], 'В работе')
        self.assertEqual(cell['total'], 2)
        self.assertEqual(cell['in_progress'], 1)
        self.assertEqual(cell['done'], 1)
        self.assertEqual(cell['overdue'], 0)
        self.assertContains(response, 'В работе')

    def test_service_internal_keys_not_rendered(self):
        self._task(DesignTask.Status.RETURNED)
        response = self._get_matrix(self.gip)
        cell = response.context['matrix_grid'][0]['cells'][1]['cell']
        # Служебные ключи §3.2 есть в данных ячейки для вычисления статуса...
        for key in ('non_cancelled', 'returned', 'on_hold', 'review'):
            self.assertIn(key, cell)
        # ...но в счётчики шаблона не выводятся: показаны ровно 4 (К3).
        self.assertContains(response, 'title="всего"')
        self.assertContains(response, 'title="в работе"')
        self.assertContains(response, 'title="просрочено"')
        self.assertContains(response, 'title="завершено"')
        self.assertNotContains(response, 'title="возвращено"')
        self.assertNotContains(response, 'title="на ожидании"')

    def test_overdue_status_when_due_date_passed(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        self._task(DesignTask.Status.ISSUED, due_date=yesterday)
        response = self._get_matrix(self.gip)
        cell = response.context['matrix_grid'][0]['cells'][1]['cell']
        self.assertEqual(cell['status'], 'OVERDUE')
        self.assertEqual(cell['overdue'], 1)
        self.assertContains(response, 'Просрочено')

    def test_diagonal_pair_rendered_as_dash_not_link(self):
        self._task(DesignTask.Status.DRAFT, source=self.section_ar, target=self.section_ar)
        response = self._get_matrix(self.gip)
        self.assertContains(response, 'task-matrix__diag')
        self.assertNotContains(
            response,
            f'task_source={self.section_ar.pk}&amp;task_target={self.section_ar.pk}',
        )

    def test_legend_has_seven_statuses(self):
        response = self._get_matrix(self.gip)
        legend = response.context['matrix_legend']
        self.assertEqual(len(legend), 7)
        self.assertEqual(
            {item['status'] for item in legend},
            {'DONE', 'OVERDUE', 'REWORK', 'WAITING_INFO', 'REVIEW', 'IN_PROGRESS', 'NOT_STARTED'},
        )

    # ─── Пустой проект (§6.3) ────────────────────────────────────────────────

    def test_empty_project_without_sections_renders(self):
        empty = Project.objects.create(
            company=self.company,
            code='EMPTY-M3',
            name='Пустой проект',
            status='active',
            created_by=self.gip,
            gip=self.gip,
        )
        ProjectMembership.objects.create(
            project=empty, user=self.gip, role_in_project='gip',
        )
        self.client.force_login(self.gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[empty.pk]),
            {'tab': 'matrix'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-role="tasks-matrix-empty"')
