"""Модуль «Задания» (DesignTask), Этап 6 — дашборд ГИПа (спека 4.4).

Покрывает: страницу /projects/<id>/tasks/dashboard/ (сводка
task_summary_for_gip + таблица «Требуют внимания»
tasks_requiring_attention), экспорт открытых заданий в Excel
(TaskDashboardExportView), доступ «только ГИП/админ»
(can_view_all_tasks) и кнопку-вход на вкладке «Задания».
Метрики и права остаются в сервисах — вьюхи тонкие.

Этап 2 — сервисы (test_design_tasks.py), Этап 3 — вкладка
(test_design_tasks_views.py), Этап 5 — уведомления
(test_design_tasks_notify.py).
"""
from datetime import timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from storage.domain.models import (
    Company,
    DesignTask,
    DesignTaskAttachment,
    Project,
    ProjectMembership,
    Section,
    UserProfile,
)
from storage.domain.services import (
    add_task_attachment,
    create_task,
    issue_task,
    task_summary_for_gip,
    tasks_requiring_attention,
    update_task_status,
)

User = get_user_model()

# Фиксированное число SQL-запросов страницы дашборда (assertNumQueries,
# п. 5.6 плана Этапа 6): рост числа = регресс N+1. Состав на 7fd3f92:
# 4 инфраструктурных (сессия, пользователь, профиль, компания в топбаре),
# 1 выборка проекта, 3 — сводка (count + группировка + просрочка),
# 1 — «Требуют внимания» (единственный запрос с select_related),
# 3 — контекст-процессоры (уведомления и счётчики замечаний).
# +1 — состав проекта (ProjectMembership) для проверки прав ГИП
# (_is_project_gip, перевод ГИП на состав). Константа, от числа задач
# не зависит.
DASHBOARD_PAGE_QUERIES = 23


class DashboardTestBase(TestCase):
    """Фикстуры — по образцу DesignTaskViewsTestBase (Этап 3)."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания Дашборд')
        self.other_company = Company.objects.create(name='Чужая компания')

        self.gip = User.objects.create_user('gip_dash', password='pass')
        self._profile(self.gip, 'gip')

        self.company_admin = User.objects.create_user('admin_dash', password='pass')
        self._profile(self.company_admin, 'admin')

        self.superuser = User.objects.create_superuser(
            'root_dash', 'root@example.com', password='pass',
        )

        self.author = User.objects.create_user('author_dash', password='pass')
        self._profile(self.author, 'developer')

        self.assignee = User.objects.create_user('assignee_dash', password='pass')
        self._profile(self.assignee, 'developer')

        self.viewer = User.objects.create_user('viewer_dash', password='pass')
        self._profile(self.viewer, 'viewer')

        self.contractor = User.objects.create_user('contractor_dash', password='pass')
        self._profile(self.contractor, 'contractor')

        self.outsider = User.objects.create_user('outsider_dash', password='pass')
        self._profile(self.outsider, 'developer', company=self.other_company)

        self.project = Project.objects.create(
            company=self.company,
            code='DASH-1',
            name='Проект дашборда',
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
        self.author.userprofile.allowed_sections.add(self.section_ar)
        self.assignee.userprofile.allowed_sections.add(self.section_kr)

        self._membership(self.author, 'developer')
        self._membership(self.assignee, 'developer')
        self._membership(self.viewer, 'viewer')
        self._membership(self.contractor, 'contractor')

        self.project_detail_url = reverse(
            'storage:project_detail', args=[self.project.pk],
        )
        self.dashboard_url = reverse(
            'storage:task_dashboard', args=[self.project.pk],
        )
        self.export_url = reverse(
            'storage:task_dashboard_export', args=[self.project.pk],
        )

    def _profile(self, user, role, company=None):
        UserProfile.objects.update_or_create(
            user=user,
            defaults={
                'role': role,
                'company': company or self.company,
            },
        )

    def _membership(self, user, role_in_project):
        return ProjectMembership.objects.create(
            project=self.project, user=user, role_in_project=role_in_project,
        )

    def _make_task(self, title='Передать нагрузки', due_date=None, **kwargs):
        """Задание АР → КР: автор в АР, исполнитель в КР."""
        params = dict(
            description='Учесть нагрузки от АР в расчёте КР',
            due_date=due_date,
            assignee=self.assignee,
        )
        params.update(kwargs)
        return create_task(
            self.author, self.project, self.section_ar, self.section_kr,
            title, params.pop('description'), **params,
        )

    # Драйверы статусов — только через сервисы Этапа 2 (матрица).
    def _in_progress(self, task):
        issue_task(self.gip, task)
        update_task_status(self.assignee, task, DesignTask.Status.IN_PROGRESS)

    def _completed(self, task):
        # COMPLETED требует output-вложения (условие матрицы Этапа 2).
        self._in_progress(task)
        add_task_attachment(
            self.assignee, task, DesignTaskAttachment.Kind.OUTPUT,
            file=SimpleUploadedFile('result.txt', b'ok'),
        )
        update_task_status(self.assignee, task, DesignTask.Status.COMPLETED)

    def _review(self, task):
        self._completed(task)
        update_task_status(self.gip, task, DesignTask.Status.REVIEW)

    def _accepted(self, task):
        self._review(task)
        update_task_status(self.author, task, DesignTask.Status.ACCEPTED)


class TaskDashboardAccessTests(DashboardTestBase):
    """Доступ: только ГИП проекта / админ / superuser (§5 матрица)."""

    def test_gip_sees_dashboard(self):
        self.client.force_login(self.gip)
        self.assertEqual(self.client.get(self.dashboard_url).status_code, 200)

    def test_company_admin_sees_dashboard(self):
        self.client.force_login(self.company_admin)
        self.assertEqual(self.client.get(self.dashboard_url).status_code, 200)

    def test_superuser_sees_dashboard(self):
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(self.dashboard_url).status_code, 200)

    def test_participants_get_403(self):
        """Участники без роли ГИП/админ — 403 (страница и экспорт)."""
        for user in (self.author, self.assignee, self.viewer, self.contractor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(
                    self.client.get(self.dashboard_url).status_code, 403,
                )
                self.assertEqual(
                    self.client.get(self.export_url).status_code, 403,
                )

    def test_outsider_gets_404(self):
        """Проект другой компании не виден через get_accessible_projects."""
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.dashboard_url).status_code, 404)
        self.assertEqual(self.client.get(self.export_url).status_code, 404)

    def test_anonymous_redirects_to_login(self):
        self.assertEqual(self.client.get(self.dashboard_url).status_code, 302)
        self.assertEqual(self.client.get(self.export_url).status_code, 302)

    def test_export_gip_and_admin_get_200(self):
        for user in (self.gip, self.company_admin, self.superuser):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(
                    self.client.get(self.export_url).status_code, 200,
                )


class TaskDashboardSummaryTests(DashboardTestBase):
    """Сводка: total, by_status, overdue, on_hold, awaiting_review."""

    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        # DRAFT без срока — не просрочено.
        self._make_task(title='Черновик')
        # ISSUED со сроком «сегодня» — граница: просрочки ещё нет.
        issued = self._make_task(title='Выдано на сегодня', due_date=today)
        issue_task(self.gip, issued)
        # IN_PROGRESS: одна просрочена, одна в сроке.
        self._in_progress(self._make_task(
            title='Просрочена в работе', due_date=today - timedelta(days=1),
        ))
        self._in_progress(self._make_task(
            title='В сроке', due_date=today + timedelta(days=5),
        ))
        # ON_HOLD — «на уточнении».
        hold = self._make_task(title='Пауза', due_date=today + timedelta(days=10))
        self._in_progress(hold)
        update_task_status(
            self.assignee, hold, DesignTask.Status.ON_HOLD,
            comment='Жду исходные данные',
        )
        # REVIEW с прошедшим сроком: и «просрочено», и «на проверке».
        self._review(self._make_task(
            title='Просрочена на проверке', due_date=today - timedelta(days=3),
        ))
        # COMPLETED с прошедшим сроком — просрочка не исчезает.
        self._completed(self._make_task(
            title='Выполнено с просрочкой', due_date=today - timedelta(days=2),
        ))
        # Терминальные статусы в просрочку не попадают.
        self._accepted(self._make_task(
            title='Принято с просрочкой', due_date=today - timedelta(days=5),
        ))
        cancelled = self._make_task(
            title='Отменено с просрочкой', due_date=today - timedelta(days=7),
        )
        issue_task(self.gip, cancelled)
        update_task_status(
            self.gip, cancelled, DesignTask.Status.CANCELLED,
            comment='Отмена заказчиком',
        )

    def test_summary_counts(self):
        self.client.force_login(self.gip)
        response = self.client.get(self.dashboard_url)
        summary = response.context['summary']
        self.assertEqual(summary['total'], 9)
        self.assertEqual(summary['overdue'], 3)
        self.assertEqual(summary['on_hold'], 1)
        self.assertEqual(summary['awaiting_review'], 1)
        by_status = summary['by_status']
        self.assertEqual(by_status.get(DesignTask.Status.DRAFT), 1)
        self.assertEqual(by_status.get(DesignTask.Status.IN_PROGRESS), 2)
        self.assertEqual(by_status.get(DesignTask.Status.ACCEPTED), 1)
        self.assertEqual(by_status.get(DesignTask.Status.CANCELLED), 1)

    def test_summary_cards_mapping(self):
        """Карточки сводки соответствуют меткам спеки 4.4."""
        self.client.force_login(self.gip)
        response = self.client.get(self.dashboard_url)
        cards = {card['key']: card['value'] for card in response.context['summary_cards']}
        self.assertEqual(
            list(cards),
            ['total', 'in_progress', 'awaiting_review', 'overdue', 'on_hold'],
        )
        self.assertEqual(cards, {
            'total': 9,
            'in_progress': 2,
            'awaiting_review': 1,
            'overdue': 3,
            'on_hold': 1,
        })

    def test_overdue_boundary_today(self):
        """due_date == сегодня — не просрочено (см. ISSUED в setUp)."""
        summary = task_summary_for_gip(self.project)
        issued = DesignTask.objects.get(title='Выдано на сегодня')
        self.assertEqual(issued.due_date, timezone.localdate())
        self.assertEqual(summary['overdue'], 3)

    def test_status_chips_cover_all_statuses(self):
        self.client.force_login(self.gip)
        response = self.client.get(self.dashboard_url)
        chips = response.context['status_chips']
        self.assertEqual(len(chips), 9)
        by_value = {chip['value']: chip['count'] for chip in chips}
        self.assertEqual(by_value[DesignTask.Status.IN_PROGRESS], 2)
        self.assertEqual(by_value[DesignTask.Status.REVIEW], 1)


class TaskDashboardAttentionTests(DashboardTestBase):
    """Таблица «Требуют внимания»: причины, дубли, порядок, терминальные."""

    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        # Просроченный IN_PROGRESS (срок старше, чем у остальных overdue).
        self.overdue_task = self._make_task(
            title='Просрочена в работе', due_date=today - timedelta(days=2),
        )
        self._in_progress(self.overdue_task)
        # REVIEW с будущим сроком — причина review.
        self.review_task = self._make_task(
            title='На проверке', due_date=today + timedelta(days=3),
        )
        self._review(self.review_task)
        # ON_HOLD — причина on_hold.
        self.hold_task = self._make_task(
            title='Пауза', due_date=today + timedelta(days=10),
        )
        self._in_progress(self.hold_task)
        update_task_status(
            self.assignee, self.hold_task, DesignTask.Status.ON_HOLD,
            comment='Жду исходные данные',
        )
        # REVIEW с прошедшим сроком — ОДНА строка с причиной overdue.
        self.review_overdue_task = self._make_task(
            title='Просрочена на проверке', due_date=today - timedelta(days=3),
        )
        self._review(self.review_overdue_task)
        # COMPLETED с прошедшим сроком присутствует (консистентно со сводкой).
        self.completed_task = self._make_task(
            title='Выполнено с просрочкой', due_date=today - timedelta(days=1),
        )
        self._completed(self.completed_task)
        # Терминальные — отсутствуют.
        self.accepted_task = self._make_task(
            title='Принято с просрочкой', due_date=today - timedelta(days=5),
        )
        self._accepted(self.accepted_task)
        self.cancelled_task = self._make_task(
            title='Отменено с просрочкой', due_date=today - timedelta(days=7),
        )
        issue_task(self.gip, self.cancelled_task)
        update_task_status(
            self.gip, self.cancelled_task, DesignTask.Status.CANCELLED,
            comment='Отмена заказчиком',
        )

    def _attention(self):
        self.client.force_login(self.gip)
        return list(self.client.get(self.dashboard_url).context['attention'])

    def test_rows_and_reasons(self):
        attention = self._attention()
        pks = [task.pk for task in attention]
        self.assertIn(self.overdue_task.pk, pks)
        self.assertIn(self.review_task.pk, pks)
        self.assertIn(self.hold_task.pk, pks)
        self.assertIn(self.review_overdue_task.pk, pks)
        self.assertIn(self.completed_task.pk, pks)
        # Терминальные статусы в таблице не нужны.
        self.assertNotIn(self.accepted_task.pk, pks)
        self.assertNotIn(self.cancelled_task.pk, pks)
        row = next(t for t in attention if t.pk == self.overdue_task.pk)
        self.assertEqual(row.attention_reason, 'overdue')
        self.assertTrue(row.is_overdue)
        self.assertEqual(row.overdue_days, 2)
        row = next(t for t in attention if t.pk == self.review_task.pk)
        self.assertEqual(row.attention_reason, 'review')
        self.assertFalse(row.is_overdue)
        row = next(t for t in attention if t.pk == self.hold_task.pk)
        self.assertEqual(row.attention_reason, 'on_hold')

    def test_overdue_priority_no_duplicates(self):
        """REVIEW с прошедшим сроком — одна строка, причина overdue."""
        attention = self._attention()
        pks = [task.pk for task in attention]
        self.assertEqual(pks.count(self.review_overdue_task.pk), 1)
        row = next(t for t in attention if t.pk == self.review_overdue_task.pk)
        self.assertEqual(row.attention_reason, 'overdue')
        self.assertEqual(row.overdue_days, 3)

    def test_group_ordering(self):
        """Порядок: overdue → review → on_hold; внутри группы по сроку."""
        attention = self._attention()
        self.assertEqual(
            [task.attention_reason for task in attention],
            ['overdue', 'overdue', 'overdue', 'review', 'on_hold'],
        )
        overdue_dates = [
            task.due_date for task in attention
            if task.attention_reason == 'overdue'
        ]
        self.assertEqual(overdue_dates, sorted(overdue_dates))

    def test_empty_attention_message(self):
        """Без «проблемных» заданий — пустое состояние, а не пустая таблица."""
        for task in DesignTask.objects.filter(project=self.project):
            if task.status not in (
                DesignTask.Status.ACCEPTED, DesignTask.Status.CANCELLED,
            ):
                update_task_status(
                    self.gip, task, DesignTask.Status.CANCELLED,
                    comment='Очистка для теста пустого состояния',
                )
        self.client.force_login(self.gip)
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.context['attention'], [])
        self.assertContains(response, 'Просроченных и требующих проверки заданий нет')


class TaskDashboardExportTests(DashboardTestBase):
    """Экспорт открытых заданий в Excel (открытые — D4 плана)."""

    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        self.draft_task = self._make_task(title='Черновик')
        self.issued_task = self._make_task(
            title='Выдано с просрочкой', due_date=today - timedelta(days=1),
        )
        issue_task(self.gip, self.issued_task)
        self.progress_task = self._make_task(
            title='В сроке', due_date=today + timedelta(days=5),
        )
        self._in_progress(self.progress_task)
        self.hold_task = self._make_task(
            title='Пауза', due_date=today + timedelta(days=2),
        )
        self._in_progress(self.hold_task)
        update_task_status(
            self.assignee, self.hold_task, DesignTask.Status.ON_HOLD,
            comment='Жду исходные данные',
        )
        self.review_task = self._make_task(
            title='Просрочена на проверке', due_date=today - timedelta(days=4),
        )
        self._review(self.review_task)
        self.completed_task = self._make_task(
            title='Выполнено с просрочкой', due_date=today - timedelta(days=1),
        )
        self._completed(self.completed_task)
        self.accepted_task = self._make_task(
            title='Принято с просрочкой', due_date=today - timedelta(days=9),
        )
        self._accepted(self.accepted_task)
        self.cancelled_task = self._make_task(
            title='Отменено с просрочкой', due_date=today - timedelta(days=9),
        )
        issue_task(self.gip, self.cancelled_task)
        update_task_status(
            self.gip, self.cancelled_task, DesignTask.Status.CANCELLED,
            comment='Отмена заказчиком',
        )

    def _export_workbook(self, response):
        return load_workbook(BytesIO(b''.join(response.streaming_content)))

    def test_export_headers_and_rows(self):
        self.client.force_login(self.gip)
        response = self.client.get(self.export_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'application/vnd.openxmlformats-officedocument'
            '.spreadsheetml.sheet',
            response['Content-Type'],
        )
        self.assertIn('reestr_zadanij_DASH-1', response['Content-Disposition'])
        sheet = self._export_workbook(response).active
        self.assertEqual(sheet.title, 'Открытые задания')
        self.assertEqual(sheet.cell(row=1, column=1).value, '№')
        self.assertEqual(sheet.cell(row=1, column=2).value, 'Задание')
        self.assertEqual(sheet.cell(row=1, column=10).value, 'Просрочено')
        self.assertEqual(sheet.cell(row=1, column=12).value, 'Суть (кратко)')
        # Шапка + 6 открытых заданий (DRAFT…COMPLETED, без терминальных).
        self.assertEqual(sheet.max_row, 7)
        statuses = {
            sheet.cell(row=row, column=8).value for row in range(2, 8)
        }
        self.assertEqual(statuses, {
            'Черновик', 'Выдано', 'В работе', 'Приостановлено',
            'На проверке', 'Выполнено',
        })
        numbers = [sheet.cell(row=row, column=1).value for row in range(2, 8)]
        self.assertEqual(numbers, [1, 2, 3, 4, 5, 6])

    def test_export_hyperlinks_to_task_cards(self):
        self.client.force_login(self.gip)
        sheet = self._export_workbook(self.client.get(self.export_url)).active
        for row in range(2, 8):
            cell = sheet.cell(row=row, column=2)
            self.assertIn(
                f'/projects/{self.project.pk}/tasks/',
                cell.hyperlink.target,
            )
            self.assertIn('#', cell.value)

    def test_export_overdue_red_font(self):
        self.client.force_login(self.gip)
        sheet = self._export_workbook(self.client.get(self.export_url)).active
        overdue_row = next(
            row for row in range(2, 8)
            if sheet.cell(row=row, column=2).value.startswith('Выдано с просрочкой')
        )
        overdue_cell = sheet.cell(row=overdue_row, column=10)
        self.assertEqual(overdue_cell.value, 'да')
        self.assertTrue(overdue_cell.font.bold)
        self.assertTrue(
            str(overdue_cell.font.color.rgb).endswith('B02A37'),
        )
        # Не просроченные строки — колонка «Просрочено» пуста
        # (openpyxl сохраняет пустую строку как пустую ячейку).
        fresh_row = next(
            row for row in range(2, 8)
            if sheet.cell(row=row, column=2).value.startswith('В сроке')
        )
        self.assertFalse(sheet.cell(row=fresh_row, column=10).value)

    def test_export_empty_project_header_only(self):
        empty_project = Project.objects.create(
            company=self.company,
            code='EMPTY-1',
            name='Пустой проект',
            status='active',
            created_by=self.gip,
        )
        ProjectMembership.objects.update_or_create(project=empty_project, user=self.gip, defaults={'role_in_project': 'gip'})
        url = reverse('storage:task_dashboard_export', args=[empty_project.pk])
        self.client.force_login(self.gip)
        sheet = self._export_workbook(self.client.get(url)).active
        self.assertEqual(sheet.max_row, 1)


class TaskDashboardEntryTests(DashboardTestBase):
    """Кнопка-вход «Дашборд» на вкладке «Задания» (контекст
    can_view_all_tasks, Этап 3 не ломается)."""

    def test_gip_sees_dashboard_link(self):
        self.client.force_login(self.gip)
        response = self.client.get(self.project_detail_url, {'tab': 'tasks'})
        self.assertContains(
            response, reverse('storage:task_dashboard', args=[self.project.pk]),
        )
        self.assertContains(response, 'task-dashboard-link')

    def test_assignee_does_not_see_link(self):
        self.client.force_login(self.assignee)
        response = self.client.get(self.project_detail_url, {'tab': 'tasks'})
        self.assertNotContains(response, 'task-dashboard-link')
        self.assertNotContains(
            response, reverse('storage:task_dashboard', args=[self.project.pk]),
        )


class TaskDashboardQueriesTests(DashboardTestBase):
    """Перф-смоук (п. 5.6 плана): сводка и таблица — единичные запросы."""

    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        for index in range(3):
            task = self._make_task(
                title=f'Просрочено {index}',
                due_date=today - timedelta(days=index + 1),
            )
            self._in_progress(task)

    def test_tasks_requiring_attention_single_query(self):
        with self.assertNumQueries(2):
            tasks_requiring_attention(self.project)

    def test_summary_three_queries(self):
        """Сводка Этапа 2: count + группировка + просрочка, без N+1."""
        with self.assertNumQueries(3):
            task_summary_for_gip(self.project)

    def test_page_queries_pinned(self):
        """Страница целиком — фиксированное число запросов (см.
        DASHBOARD_PAGE_QUERIES); рост = регресс N+1."""
        self.client.force_login(self.gip)
        with self.assertNumQueries(DASHBOARD_PAGE_QUERIES):
            self.client.get(self.dashboard_url)
