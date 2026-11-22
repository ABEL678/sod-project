"""Тесты ORM-агрегации «Матрицы заданий» (этап M2).

Покрывает сервис `tasks_matrix_for_project` (§3.3 плана): оси без COMMON,
один GROUP BY-запрос, счётчики Ф3, 7 статусов ячейки на живых данных,
границы Ф1/Ф2 и предикат просрочки (строгое `<`).
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from storage.domain.models import (
    ProjectMembership,
    Company,
    DesignTask,
    Project,
    Section,
    UserProfile,
)
from storage.domain.services import tasks_matrix_for_project
from storage.domain.tasks_matrix import (
    CELL_STATUS_DONE,
    CELL_STATUS_IN_PROGRESS,
    CELL_STATUS_NOT_STARTED,
    CELL_STATUS_OVERDUE,
    CELL_STATUS_REVIEW,
    CELL_STATUS_REWORK,
    CELL_STATUS_WAITING_INFO,
)

User = get_user_model()


class TasksMatrixForProjectTests(TestCase):
    """Фикстуры: две оси (АР, КР) + COMMON; задачи создаются напрямую."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания Матрицы')

        self.gip = User.objects.create_user('gip_matrix', password='pass')
        UserProfile.objects.update_or_create(
            user=self.gip, defaults={'role': 'gip', 'company': self.company},
        )

        self.project = Project.objects.create(
            company=self.company,
            code='MX-1',
            name='Проект матрицы',
            status='active',
            created_by=self.gip,
        )
        ProjectMembership.objects.update_or_create(project=self.project, user=self.gip, defaults={'role_in_project': 'gip'})
        self.section_ar = Section.objects.create(
            project=self.project, code='AR', name='АР', order=1,
        )
        self.section_kr = Section.objects.create(
            project=self.project, code='KR', name='КР', order=2,
        )
        self.section_common = Section.objects.create(
            project=self.project, code='COMMON', name='Общие', order=99,
        )

        self.today = timezone.localdate()

    def _task(self, status, due_date=None, source=None, target=None, **kwargs):
        params = dict(
            project=self.project,
            source_section=source or self.section_ar,
            target_section=target or self.section_kr,
            author=self.gip,
            title='Задание',
            description='Описание',
            status=status,
            due_date=due_date,
        )
        params.update(kwargs)
        return DesignTask.objects.create(**params)

    def _cells(self, **kwargs):
        return tasks_matrix_for_project(self.project, **kwargs)

    # ─── Оси решётки ─────────────────────────────────────────────────────────

    def test_axes_exclude_common(self):
        result = self._cells()

        codes = [section.code for section in result['sections']]
        self.assertEqual(codes, ['AR', 'KR'])
        self.assertNotIn('COMMON', codes)

    def test_axes_sorted_by_order_then_code(self):
        Section.objects.create(project=self.project, code='AA', name='АА', order=5)
        Section.objects.create(project=self.project, code='ZZ', name='ЗЗ', order=5)

        codes = [s.code for s in self._cells()['sections']]
        self.assertEqual(codes, ['AR', 'KR', 'AA', 'ZZ'])

    def test_empty_project_has_axes_and_no_cells(self):
        result = self._cells()

        self.assertEqual(len(result['sections']), 2)
        self.assertEqual(result['cells'], {})
        self.assertEqual(result['today'], self.today)

    def test_single_pair_gives_single_cell(self):
        self._task(DesignTask.Status.IN_PROGRESS)

        cells = self._cells()['cells']

        self.assertEqual(len(cells), 1)
        self.assertIn((self.section_ar.pk, self.section_kr.pk), cells)

    def test_task_with_common_section_not_in_cells(self):
        self._task(DesignTask.Status.IN_PROGRESS, target=self.section_common)

        self.assertEqual(self._cells()['cells'], {})

    # ─── Счётчики Ф3 ─────────────────────────────────────────────────────────

    def test_counters_match_facts(self):
        self._task(DesignTask.Status.COMPLETED)
        self._task(DesignTask.Status.ACCEPTED)
        self._task(DesignTask.Status.IN_PROGRESS)
        self._task(DesignTask.Status.CANCELLED)

        cell = self._cells()['cells'][(self.section_ar.pk, self.section_kr.pk)]

        self.assertEqual(cell['total'], 3)          # не-CANCELLED
        self.assertEqual(cell['non_cancelled'], 3)
        self.assertEqual(cell['done'], 2)
        self.assertEqual(cell['in_progress'], 1)
        self.assertEqual(cell['overdue'], 0)
        self.assertEqual(cell['status'], CELL_STATUS_IN_PROGRESS)

    # ─── Все 7 статусов ячейки на живых данных ───────────────────────────────

    def _single_cell_status(self):
        cells = self._cells()['cells']
        return cells[(self.section_ar.pk, self.section_kr.pk)]['status']

    def test_status_done(self):
        self._task(DesignTask.Status.COMPLETED)
        self._task(DesignTask.Status.ACCEPTED)

        self.assertEqual(self._single_cell_status(), CELL_STATUS_DONE)

    def test_status_overdue(self):
        yesterday = self.today - timedelta(days=1)
        self._task(DesignTask.Status.IN_PROGRESS, due_date=yesterday)

        self.assertEqual(self._single_cell_status(), CELL_STATUS_OVERDUE)

    def test_status_rework(self):
        self._task(DesignTask.Status.RETURNED)

        self.assertEqual(self._single_cell_status(), CELL_STATUS_REWORK)

    def test_status_waiting_info(self):
        self._task(DesignTask.Status.ON_HOLD)

        self.assertEqual(self._single_cell_status(), CELL_STATUS_WAITING_INFO)

    def test_status_review(self):
        self._task(DesignTask.Status.REVIEW)

        self.assertEqual(self._single_cell_status(), CELL_STATUS_REVIEW)

    def test_status_in_progress(self):
        self._task(DesignTask.Status.IN_PROGRESS)

        self.assertEqual(self._single_cell_status(), CELL_STATUS_IN_PROGRESS)

    def test_status_not_started(self):
        self._task(DesignTask.Status.DRAFT)
        self._task(DesignTask.Status.ISSUED)

        self.assertEqual(self._single_cell_status(), CELL_STATUS_NOT_STARTED)

    # ─── Предикат просрочки (строгое `<`) ────────────────────────────────────

    def test_due_date_today_is_not_overdue(self):
        self._task(DesignTask.Status.IN_PROGRESS, due_date=self.today)

        cell = self._cells()['cells'][(self.section_ar.pk, self.section_kr.pk)]

        self.assertEqual(cell['overdue'], 0)
        self.assertEqual(cell['status'], CELL_STATUS_IN_PROGRESS)

    def test_due_date_yesterday_is_overdue(self):
        yesterday = self.today - timedelta(days=1)
        self._task(DesignTask.Status.IN_PROGRESS, due_date=yesterday)

        cell = self._cells()['cells'][(self.section_ar.pk, self.section_kr.pk)]

        self.assertEqual(cell['overdue'], 1)
        self.assertEqual(cell['status'], CELL_STATUS_OVERDUE)

    def test_due_date_without_value_is_not_overdue(self):
        self._task(DesignTask.Status.DRAFT, due_date=None)

        cell = self._cells()['cells'][(self.section_ar.pk, self.section_kr.pk)]

        self.assertEqual(cell['overdue'], 0)

    def test_today_parameter_shifts_overdue(self):
        task_date = self.today
        self._task(DesignTask.Status.IN_PROGRESS, due_date=task_date)

        # Сегодня — не просрочено.
        cell = self._cells()['cells'][(self.section_ar.pk, self.section_kr.pk)]
        self.assertEqual(cell['overdue'], 0)

        # Завтра относительно срока — просрочено, без правки данных.
        tomorrow = self.today + timedelta(days=1)
        cell = self._cells(today=tomorrow)['cells'][
            (self.section_ar.pk, self.section_kr.pk)
        ]
        self.assertEqual(cell['overdue'], 1)
        self.assertEqual(cell['status'], CELL_STATUS_OVERDUE)

    def test_overdue_suppressed_by_review(self):
        yesterday = self.today - timedelta(days=1)
        self._task(DesignTask.Status.REVIEW, due_date=yesterday)

        cell = self._cells()['cells'][(self.section_ar.pk, self.section_kr.pk)]

        self.assertEqual(cell['overdue'], 0)
        self.assertEqual(cell['status'], CELL_STATUS_REVIEW)

    # ─── Ф1 / Ф2 на живых объектах ───────────────────────────────────────────

    def test_f1_pair_of_only_cancelled_is_not_started(self):
        self._task(DesignTask.Status.CANCELLED)
        self._task(DesignTask.Status.CANCELLED)

        cell = self._cells()['cells'][(self.section_ar.pk, self.section_kr.pk)]

        self.assertEqual(cell['total'], 0)
        self.assertEqual(cell['done'], 0)
        self.assertEqual(cell['status'], CELL_STATUS_NOT_STARTED)

    def test_f2_draft_plus_cancelled_is_not_started(self):
        self._task(DesignTask.Status.DRAFT)
        self._task(DesignTask.Status.CANCELLED)

        cell = self._cells()['cells'][(self.section_ar.pk, self.section_kr.pk)]

        self.assertEqual(cell['total'], 1)
        self.assertEqual(cell['status'], CELL_STATUS_NOT_STARTED)

    def test_partial_completion_is_not_started(self):
        self._task(DesignTask.Status.COMPLETED)
        self._task(DesignTask.Status.DRAFT)

        cell = self._cells()['cells'][(self.section_ar.pk, self.section_kr.pk)]

        self.assertEqual(cell['done'], 1)
        self.assertEqual(cell['total'], 2)
        self.assertEqual(cell['status'], CELL_STATUS_NOT_STARTED)

    # ─── Пары и группировка ──────────────────────────────────────────────────

    def test_two_pairs_are_separate_cells(self):
        self._task(DesignTask.Status.IN_PROGRESS)
        self._task(
            DesignTask.Status.RETURNED,
            source=self.section_kr,
            target=self.section_ar,
        )

        cells = self._cells()['cells']

        self.assertEqual(cells[(self.section_ar.pk, self.section_kr.pk)]['status'],
                         CELL_STATUS_IN_PROGRESS)
        self.assertEqual(cells[(self.section_kr.pk, self.section_ar.pk)]['status'],
                         CELL_STATUS_REWORK)

    def test_diagonal_pair_is_regular_cell(self):
        self._task(
            DesignTask.Status.COMPLETED,
            source=self.section_ar,
            target=self.section_ar,
        )

        cells = self._cells()['cells']

        self.assertIn((self.section_ar.pk, self.section_ar.pk), cells)
        self.assertEqual(cells[(self.section_ar.pk, self.section_ar.pk)]['status'],
                         CELL_STATUS_DONE)

    def test_cells_of_other_project_are_not_mixed(self):
        other = Project.objects.create(
            company=self.company,
            code='MX-2',
            name='Другой проект',
            status='active',
            created_by=self.gip,
        )
        ProjectMembership.objects.update_or_create(project=other, user=self.gip, defaults={'role_in_project': 'gip'})
        Section.objects.create(project=other, code='AR', name='АР', order=1)
        DesignTask.objects.create(
            project=other,
            source_section=Section.objects.get(project=other, code='AR'),
            target_section=Section.objects.get(project=other, code='AR'),
            author=self.gip,
            title='Чужое',
            description='Описание',
            status=DesignTask.Status.IN_PROGRESS,
        )

        self.assertEqual(self._cells()['cells'], {})


class TasksMatrixQueryCountTests(TestCase):
    """СТОП-2: одна агрегация GROUP BY на вызов (§3.3, §3.5 вариант А)."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания Запросов')
        self.gip = User.objects.create_user('gip_matrix_q', password='pass')
        UserProfile.objects.update_or_create(
            user=self.gip, defaults={'role': 'gip', 'company': self.company},
        )
        self.project = Project.objects.create(
            company=self.company,
            code='MQ-1',
            name='Проект запросов',
            status='active',
            created_by=self.gip,
        )
        ProjectMembership.objects.update_or_create(project=self.project, user=self.gip, defaults={'role_in_project': 'gip'})
        self.ar = Section.objects.create(project=self.project, code='AR', name='АР', order=1)
        self.kr = Section.objects.create(project=self.project, code='KR', name='КР', order=2)
        for index in range(5):
            DesignTask.objects.create(
                project=self.project,
                source_section=self.ar,
                target_section=self.kr,
                author=self.gip,
                title=f'Задание {index}',
                description='Описание',
                status=DesignTask.Status.DRAFT,
            )

    def test_exactly_two_queries_axes_and_one_group_by(self):
        with self.assertNumQueries(2):
            result = tasks_matrix_for_project(self.project)

        self.assertEqual(len(result['cells']), 1)

    def test_aggregation_query_uses_group_by(self):
        with CaptureQueriesContext(connection) as ctx:
            tasks_matrix_for_project(self.project)

        group_by_queries = [
            q['sql'] for q in ctx.captured_queries if 'GROUP BY' in q['sql'].upper()
        ]
        self.assertEqual(len(group_by_queries), 1)

    def test_group_by_query_filters_by_project(self):
        with CaptureQueriesContext(connection) as ctx:
            tasks_matrix_for_project(self.project)

        group_by_sql = next(
            q['sql'] for q in ctx.captured_queries
            if 'GROUP BY' in q['sql'].upper()
        )
        self.assertIn('"project_id"', group_by_sql)
        self.assertIn('GROUP BY', group_by_sql.upper())
