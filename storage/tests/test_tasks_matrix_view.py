"""Вьюха и шаблон «Матрицы заданий» (этап M3).

Покрывает план §4.2 (вкладка ?tab=matrix) и §6.3 (вьюхи): 200 для ГИПа,
откат на documents для не-ГИПа (якорь скрыт, данные не утекают), 404 вне
доступных проектов, решётка из осей без COMMON, пустой проект без
ошибок. Гейт прав — во вьюхе (can_view_all_tasks), сервис прав не проверяет.
"""

import re
from datetime import timedelta
from pathlib import Path

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
        )
        ProjectMembership.objects.update_or_create(project=self.project, user=self.gip, defaults={'role_in_project': 'gip'})
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
        )
        ProjectMembership.objects.update_or_create(project=project_other, user=self.outsider, defaults={'role_in_project': 'gip'})
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
        )
        ProjectMembership.objects.update_or_create(project=empty, user=self.gip, defaults={'role_in_project': 'gip'})
        self.client.force_login(self.gip)
        response = self.client.get(
            reverse('storage:project_detail', args=[empty.pk]),
            {'tab': 'matrix'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-role="tasks-matrix-empty"')

    # ─── M3.2: порядок якорей, диагональ, итоги, плашки, хинты, SQL ──────────

    def test_matrix_anchor_placed_right_after_tasks(self):
        """Якорь «Матрица» — сразу после «Задания», до «Запросы» (§4.2 п.5)."""
        self.client.force_login(self.gip)
        response = self.client.get(self.url, {'tab': 'tasks'})
        html = response.content.decode('utf-8')
        pos_tasks = html.index('href="%s?tab=tasks"' % self.url)
        pos_matrix = html.index('href="%s?tab=matrix"' % self.url)
        pos_rfi = html.index('href="%s?tab=rfi"' % self.url)
        self.assertLess(pos_tasks, pos_matrix)
        self.assertLess(pos_matrix, pos_rfi)

    def test_diagonal_cell_is_inactive_and_titled(self):
        """Диагональ: без ссылки, title «Та же дисциплина» (2а, B, E)."""
        self._task(DesignTask.Status.DRAFT, source=self.section_ar, target=self.section_ar)
        response = self._get_matrix(self.gip)
        html = response.content.decode('utf-8')
        self.assertIn('td class="text-center task-matrix__diag"', html)
        self.assertIn('title="Та же дисциплина"', html)
        self.assertIn('aria-label="Та же дисциплина"', html)
        self.assertNotIn('task_source=%d&amp;task_target=%d' % (
            self.section_ar.pk, self.section_ar.pk), html)

    def test_empty_pair_hint(self):
        """Пустая пара: title «Заданий между разделами нет» (E)."""
        response = self._get_matrix(self.gip)
        self.assertContains(response, 'title="Заданий между разделами нет"')
        self.assertContains(response, 'aria-label="Заданий между разделами нет"')

    def test_legend_titles_are_plan_rules(self):
        """Title каждого из 7 чипов — краткое правило из плана §4.4 (E)."""
        response = self._get_matrix(self.gip)
        expected = {
            'Готово': 'все задания пары закрыты',
            'Просрочено': 'есть просрочка по сроку',
            'Доработка': 'есть возврат на доработку',
            'Пауза': 'есть пауза (жду ИД)',
            'На проверке': 'есть задание на проверке',
            'В работе': 'есть задание в работе',
            'Не начато': 'задания не начаты',
        }
        html = response.content.decode('utf-8')
        for label, rule in expected.items():
            self.assertIn(
                'title="%s: %s"' % (label, rule), html,
                msg='нет title для чипа «%s»' % label,
            )
        self.assertEqual(html.count('data-role="tasks-matrix-legend"'), 1)
        legend = response.context['matrix_legend']
        self.assertEqual(len(legend), 7)
        self.assertTrue(all(item['hint'] for item in legend))

    def test_summary_banners_formulas(self):
        """Плашки G: N/M (просрочка), K (доработка/пауза пар), L/T (завершено)."""
        yesterday = timezone.localdate() - timedelta(days=1)
        # AR→KR: 2 просрочки (N), пара overdue>=1 (M=1 пара), итог done=1 из total=3.
        self._task(DesignTask.Status.ISSUED, due_date=yesterday)
        self._task(DesignTask.Status.ISSUED, due_date=yesterday)
        self._task(DesignTask.Status.COMPLETED)
        # KR→AR: возврат (REWORK) — пара в K.
        self._task(DesignTask.Status.RETURNED, source=self.section_kr, target=self.section_ar)
        # Диагональ AR→AR: не входит ни в одну плашку.
        self._task(DesignTask.Status.IN_PROGRESS, source=self.section_ar, target=self.section_ar)
        response = self._get_matrix(self.gip)
        summary = response.context['matrix_summary']
        self.assertEqual(summary['overdue_tasks'], 2)   # N
        self.assertEqual(summary['overdue_pairs'], 1)   # M
        self.assertEqual(summary['rework_wait_pairs'], 1)  # K (видимые пары)
        self.assertEqual(summary['done_tasks'], 1)      # L
        # T: AR→KR (3 не отменённых) + KR→AR (1); диагональ AR→AR не входит.
        self.assertEqual(summary['total_tasks'], 4)
        self.assertContains(response, 'Просрочено: 2 заданий в 1 парах')
        self.assertContains(response, 'На доработке и в паузе: 1 пар')
        self.assertContains(response, 'Завершено: 1 из 4')

    def test_totals_equal_sum_of_visible_cells(self):
        """Итоги строк/столбцов == суммы видимых ячеек; диагональ не входит (G)."""
        self._task(DesignTask.Status.IN_PROGRESS)
        self._task(DesignTask.Status.COMPLETED)
        self._task(DesignTask.Status.ISSUED, due_date=timezone.localdate() - timedelta(days=1))
        self._task(DesignTask.Status.DRAFT, source=self.section_ar, target=self.section_ar)
        self._task(DesignTask.Status.RETURNED, source=self.section_kr, target=self.section_ar)
        response = self._get_matrix(self.gip)
        grid = response.context['matrix_grid']
        col_totals = response.context['matrix_col_totals']
        visible = []
        for i, row in enumerate(grid):
            row_visible = []
            for j, item in enumerate(row['cells']):
                if i != j and item['cell'] is not None:
                    row_visible.append(item['cell'])
                    visible.append(item)
            for key in ('total', 'in_progress', 'overdue', 'done'):
                self.assertEqual(
                    row['totals'][key], sum(c[key] for c in row_visible),
                    msg='итог строки %s' % key,
                )
        for j, totals in enumerate(col_totals):
            column = [
                grid[i]['cells'][j]['cell']
                for i in range(len(grid))
                if i != j and grid[i]['cells'][j]['cell'] is not None
            ]
            for key in ('total', 'in_progress', 'overdue', 'done'):
                self.assertEqual(
                    totals[key], sum(c[key] for c in column),
                    msg='итог столбца %s' % key,
                )
        # Диагональная пара (AR→AR, 1 задание) не входит в итоги.
        self.assertEqual(sum(v['cell']['total'] for v in visible), 4)
        self.assertEqual(response.context['matrix_summary']['total_tasks'], 4)
        self.assertContains(response, 'Итого по разделу-источнику')
        self.assertContains(response, 'Итого по разделу-цели')

    def test_summary_and_totals_classes_have_tab_prefix(self):
        """Классы плашек и итогов — с префиксом .task-matrix-tab (G, ЗM3-1)."""
        response = self._get_matrix(self.gip)
        html = response.content.decode('utf-8')
        self.assertIn('task-matrix__stat task-matrix__stat--overdue', html)
        self.assertIn('task-matrix__total', html)
        css_path = Path(__file__).resolve().parents[2] / 'static' / 'css' / 'redesign.css'
        css = css_path.read_text(encoding='utf-8')
        for status in ('done', 'overdue', 'rework', 'waiting_info',
                       'not_started', 'review', 'in_progress'):
            self.assertIn('.task-matrix-tab .task-matrix__cell--%s' % status, css)
        self.assertIn('.task-matrix-tab .task-matrix__stat--overdue', css)
        self.assertIn('.task-matrix-tab td.task-matrix__total', css)
        self.assertIn('.task-matrix-tab td.task-matrix__diag', css)
        # ЗM3-1: тонировка без скоупа (голый .task-matrix__cell--*) запрещена.
        self.assertIsNone(re.search(r'(?m)^\.task-matrix__cell--', css))

    def test_visible_text_has_no_latin_status_identifiers(self):
        """Подписи статусов по-русски: без COMPLETED/ACCEPTED/... в тексте (D)."""
        self._task(DesignTask.Status.COMPLETED)
        response = self._get_matrix(self.gip)
        html = response.content.decode('utf-8')
        # Весь блок вкладки матрицы (таблица, плашки, легенда, подписи).
        body = html.split('data-role="tasks-matrix"', 1)[1]
        for ident in ('COMPLETED', 'ACCEPTED', 'REVIEW', 'CANCELLED', 'IN_PROGRESS',
                      'NOT_STARTED', 'WAITING_INFO', 'REWORK', 'OVERDUE', 'DRAFT', 'ISSUED'):
            self.assertNotIn(ident, body)
        self.assertContains(response, 'Выполнено')
        self.assertContains(response, 'На проверке')
        self.assertContains(response, 'Отменено')
        self.assertNotContains(response, 'COMPLETED, ACCEPTED')

    def test_sql_queries_not_increased_by_aggregates(self):
        """G: агрегаты — Python-только, число SQL-запросов вкладки не растёт (§6.3)."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._task(DesignTask.Status.IN_PROGRESS)
        self.client.force_login(self.gip)
        # Прогрев: сессия/кэш/гейты, чтобы считать только вкладку.
        self.client.get(self.url, {'tab': 'matrix'})
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(self.url, {'tab': 'matrix'})
        self.assertEqual(response.status_code, 200)
        # Запросы матрицы: ровно одна выборка осей (storage_section, без
        # COMMON) и один GROUP BY по storage_designtask. Остальные запросы
        # (папки, документы, разделы для дерева) — общие для страницы проекта.
        designtask_sql = [
            q['sql'] for q in ctx.captured_queries
            if 'storage_designtask' in q['sql']
        ]
        self.assertEqual(len(designtask_sql), 1)
        self.assertIn('GROUP BY', designtask_sql[0].upper())


class TasksMatrixDrillDownTests(TestCase):
    """M4 (план §4.6, §6.3): drill-down пары source/target в списке заданий."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания M4')
        self.other_company = Company.objects.create(name='Чужая компания M4')
        self.gip = User.objects.create_user('gip_m4', password='pass')
        UserProfile.objects.update_or_create(
            user=self.gip, defaults={'role': 'gip', 'company': self.company},
        )
        self.developer = User.objects.create_user('dev_m4', password='pass')
        UserProfile.objects.update_or_create(
            user=self.developer, defaults={'role': 'developer', 'company': self.company},
        )
        self.project = Project.objects.create(
            company=self.company, code='DD-M4', name='Проект drill-down M4',
            status='active', created_by=self.gip,
        )
        ProjectMembership.objects.update_or_create(project=self.project, user=self.gip, defaults={'role_in_project': 'gip'})
        self.ar = Section.objects.create(project=self.project, code='AR', name='Архитектура', order=1)
        self.kr = Section.objects.create(project=self.project, code='KR', name='Конструкции', order=2)
        self.ov = Section.objects.create(project=self.project, code='OV', name='Вентиляция', order=3)
        ProjectMembership.objects.create(project=self.project, user=self.developer, role_in_project='developer')
        self.url = reverse('storage:project_detail', args=[self.project.pk])

    def _task(self, source, target, status=DesignTask.Status.DRAFT, title='Задание'):
        return DesignTask.objects.create(
            project=self.project, source_section=source, target_section=target,
            author=self.gip, title=title, description='Описание', status=status,
        )

    def _drill(self, user, **params):
        self.client.force_login(user)
        query = {'tab': 'tasks', 'role_filter': 'all'}
        query.update(params)
        return self.client.get(self.url, query)

    def _titles(self, response):
        return sorted(t.title for t in response.context['tasks'])

    def test_and_logic_two_sources_one_target_returns_only_intersection(self):
        """И-логика: AR→KR и OV→KR; drill-down AR→KR отдаёт только AR→KR."""
        self._task(self.ar, self.kr, title='ar-kr')
        self._task(self.ov, self.kr, title='ov-kr')
        self._task(self.kr, self.ar, title='kr-ar')
        response = self._drill(
            self.gip, task_source=self.ar.pk, task_target=self.kr.pk,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._titles(response), ['ar-kr'])

    def test_cancelled_excluded_from_drilldown(self):
        """Решение 1: CANCELLED не попадает в drill-down пары."""
        self._task(self.ar, self.kr, title='живое')
        self._task(self.ar, self.kr, status=DesignTask.Status.CANCELLED, title='отменено')
        response = self._drill(
            self.gip, task_source=self.ar.pk, task_target=self.kr.pk,
        )
        self.assertEqual(self._titles(response), ['живое'])

    def test_drilldown_count_matches_matrix_total_cell(self):
        """Решение 2 / Р3: «всего» ячейки == число строк drill-down (с CANCELLED)."""
        self._task(self.ar, self.kr, status=DesignTask.Status.IN_PROGRESS)
        self._task(self.ar, self.kr, status=DesignTask.Status.COMPLETED)
        self._task(self.ar, self.kr, status=DesignTask.Status.CANCELLED)
        self._task(self.ov, self.kr)  # другая пара, в ячейку AR→KR не входит
        self.client.force_login(self.gip)
        matrix = self.client.get(self.url, {'tab': 'matrix'})
        cell = matrix.context['matrix_grid'][0]['cells'][1]['cell']
        drill = self._drill(self.gip, task_source=self.ar.pk, task_target=self.kr.pk)
        self.assertEqual(cell['total'], len(drill.context['tasks']))
        self.assertEqual(cell['total'], 2)

    def test_task_section_filter_unchanged(self):
        """СТОП-4 «?task_section не сломан»: ИЛИ по разделу, как до M4."""
        self._task(self.ar, self.kr, title='ar-kr')
        self._task(self.ov, self.ar, title='ov-ar')
        self._task(self.ov, self.kr, title='ov-kr')
        response = self._drill(self.gip, task_section=self.ar.pk)
        self.assertEqual(self._titles(response), ['ar-kr', 'ov-ar'])

    def test_diagonal_pair_no_special_case_returns_200(self):
        """ЗM4-1: диагональная задача возвращается фильтром пары; спецкейса нет, ответ 200."""
        self._task(self.ar, self.ar, title='диагональ')
        response = self._drill(self.gip, task_source=self.ar.pk, task_target=self.ar.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._titles(response), ['диагональ'])

    def test_diagonal_pair_without_tasks_is_empty(self):
        response = self._drill(self.gip, task_source=self.ar.pk, task_target=self.ar.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['tasks'], [])

    def test_invalid_pair_params_ignored(self):
        """Нецифровые параметры не ломают страницу и не фильтруют."""
        self._task(self.ar, self.kr, title='ar-kr')
        response = self._drill(self.gip, task_source='abc', task_target=self.kr.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._titles(response), ['ar-kr'])
        self.assertEqual(response.context['task_source_filter'], '')

    def test_reset_clears_all_three_params(self):
        """Решение 6: кнопки «Сбросить» и «Сбросить фильтры» чистят пару и task_section."""
        self._task(self.ar, self.kr)
        self.client.force_login(self.gip)
        # Пара без заданий (AR→OV): рендерится пустое состояние с кнопкой.
        response = self.client.get(self.url, {
            'tab': 'tasks', 'role_filter': 'all',
            'task_source': self.ar.pk, 'task_target': self.ov.pk,
        })
        html = response.content.decode('utf-8')
        self.assertContains(response, 'data-role="tasks-empty"')
        self.assertIn('task_source=&amp;task_target=">Сбросить</a>', html)
        # Кнопка «Сбросить фильтры» — в пустом состоянии (атрибут href и текст
        # разделены переносом строки в шаблоне); проверяем href и текст отдельно.
        self.assertRegex(
            html,
            r'href="\?tab=tasks&amp;role_filter=all&amp;task_source=&amp;task_target="\s*>\s*Сбросить фильтры</a>',
        )
        # Пара не уходит в форму, если активна только фильтрация по статусу —
        # hidden-поля присутствуют при активной паре (сохраняются при «Применить»).
        self.assertIn('name="task_source" value="%d"' % self.ar.pk, html)

    def test_reset_empty_state_clears_pair(self):
        """Пустое состояние с активной парой: «Сбросить фильтры» чистит пару."""
        self.client.force_login(self.gip)
        response = self.client.get(self.url, {
            'tab': 'tasks', 'role_filter': 'all',
            'task_source': self.ar.pk, 'task_target': self.kr.pk,
        })
        self.assertContains(response, 'data-role="tasks-empty"')
        self.assertContains(response, 'task_source=&amp;task_target=">Сбросить фильтры</a>')

    def test_non_gip_direct_url_role_all_matches_incoming_behaviour(self):
        """Решение 7: не-ГИП с role_filter=all получает поведение incoming (без 403)."""
        mine = self._task(self.ar, self.kr, title='мне')
        mine.assignee = self.developer
        mine.save(update_fields=['assignee'])
        self._task(self.ar, self.kr, title='чужое')
        url_params = {
            'task_source': self.ar.pk, 'task_target': self.kr.pk,
        }
        forced = self._drill(self.developer, **url_params)
        incoming = self._drill(self.developer, role_filter='incoming', **url_params)
        self.assertEqual(forced.status_code, 200)
        self.assertEqual(forced.context['active_role_filter'], 'incoming')
        self.assertEqual(self._titles(forced), self._titles(incoming))
        self.assertEqual(self._titles(forced), ['мне'])
