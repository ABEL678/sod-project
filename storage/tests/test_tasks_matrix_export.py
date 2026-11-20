"""Тесты M6 «Матрицы заданий»: ИИ-сводка, агрегатор, выгрузка XLSX/PDF.

Критерии приёмки СТОП-6 (решения Координатора 1–5):
- сводка согласована с matrix_summary и ячейками (тест на равенство чисел);
- гейт выгрузки как у TaskDashboardExportView (403 для не-ГИПа, 404 вне
  доступных проектов); format обязателен, невалидное значение → 404;
- ноль зависимостей/миграций/моделей; COMMON исключён единообразно;
- XLSX читается openpyxl.load_workbook; PDF — проверка генерации без
  байтового парсинга; диагональ и термины Р8 («Сводка», без «дашборд»).
"""

import re
from datetime import timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from storage.domain.models import (
    Company,
    DesignTask,
    Project,
    ProjectMembership,
    Section,
    UserProfile,
)
from storage.domain.services import tasks_matrix_for_project
from storage.domain.tasks_matrix import (
    CELL_STATUSES,
    build_ai_summary,
    matrix_sum,
    matrix_summary,
    visible_cells,
)

User = get_user_model()


class MatrixAggregatorPureTests(SimpleTestCase):
    """Чистые функции итогов (решение 5): без БД, стиль M1."""

    def _cell(self, total, in_progress, overdue, done, status='IN_PROGRESS'):
        return {
            'total': total,
            'in_progress': in_progress,
            'overdue': overdue,
            'done': done,
            'status': status,
        }

    def test_matrix_sum_sums_four_counters(self):
        cells = [
            self._cell(3, 1, 2, 0),
            self._cell(2, 0, 1, 1),
        ]
        self.assertEqual(
            matrix_sum(cells),
            {'total': 5, 'in_progress': 1, 'overdue': 3, 'done': 1},
        )

    def test_matrix_sum_empty_list_is_zeroed(self):
        self.assertEqual(
            matrix_sum([]),
            {'total': 0, 'in_progress': 0, 'overdue': 0, 'done': 0},
        )

    def test_visible_cells_excludes_diagonal_and_missing(self):
        class FakeSection:
            def __init__(self, pk):
                self.pk = pk

        sections = [FakeSection(1), FakeSection(2)]
        cells = {
            (1, 1): self._cell(1, 1, 0, 0),
            (1, 2): self._cell(2, 0, 2, 0, status='OVERDUE'),
        }
        visible = visible_cells(sections, cells)
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]['total'], 2)

    def test_matrix_summary_five_indicators_without_diagonal(self):
        class FakeSection:
            def __init__(self, pk):
                self.pk = pk

        sections = [FakeSection(1), FakeSection(2), FakeSection(3)]
        cells = {
            (1, 2): self._cell(2, 0, 2, 0, status='OVERDUE'),
            (2, 3): self._cell(1, 1, 0, 1, status='DONE'),
            (3, 3): self._cell(9, 9, 9, 9, status='REWORK'),  # диагональ
        }
        self.assertEqual(matrix_summary(sections, cells), {
            'overdue_tasks': 2,
            'overdue_pairs': 1,
            'rework_wait_pairs': 0,
            'done_tasks': 1,
            'total_tasks': 3,
        })


class BuildAiSummaryTests(TestCase):
    """ИИ-сводка (решение 1): plain text, детерминизм, равенство чисел."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания Сводка')
        self.gip = User.objects.create_user('gip_summary', password='pass')
        UserProfile.objects.update_or_create(
            user=self.gip, defaults={'role': 'gip', 'company': self.company},
        )
        self.project = Project.objects.create(
            company=self.company,
            code='SM-1',
            name='Проект сводки',
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
        self.section_eom = Section.objects.create(
            project=self.project, code='EOM', name='Электрика', order=3,
        )
        self.section_common = Section.objects.create(
            project=self.project, code='COMMON', name='Общие', order=99,
        )
        self.today = timezone.localdate()
        self._seed_tasks()

    def _seed_tasks(self):
        overdue_date = self.today - timedelta(days=1)
        self._task(DesignTask.Status.IN_PROGRESS,
                   self.section_ar, self.section_kr)
        self._task(DesignTask.Status.DRAFT, self.section_ar, self.section_kr,
                   due_date=overdue_date)
        self._task(DesignTask.Status.ACCEPTED, self.section_kr, self.section_ar)
        self._task(DesignTask.Status.IN_PROGRESS, self.section_kr, self.section_ar)
        self._task(DesignTask.Status.RETURNED, self.section_eom, self.section_ar)
        self._task(DesignTask.Status.ON_HOLD, self.section_ar, self.section_eom)
        self._task(DesignTask.Status.REVIEW, self.section_kr, self.section_eom)
        self._task(DesignTask.Status.COMPLETED, self.section_eom, self.section_kr)
        self._task(DesignTask.Status.COMPLETED, self.section_eom, self.section_kr)
        # Вне универсума сводки: COMMON-пара и диагональ.
        self._task(DesignTask.Status.IN_PROGRESS,
                   self.section_common, self.section_ar)
        self._task(DesignTask.Status.IN_PROGRESS,
                   self.section_ar, self.section_ar)

    def _task(self, status, source, target, due_date=None):
        DesignTask.objects.create(
            project=self.project,
            source_section=source,
            target_section=target,
            author=self.gip,
            title='Задание сводки',
            description='Описание',
            status=status,
            due_date=due_date,
        )

    def _matrix(self):
        return tasks_matrix_for_project(self.project)

    def test_summary_numbers_equal_matrix_summary_and_cells(self):
        """Ключевой критерий СТОП-6: числа сводки == matrix_summary == ячейки."""
        matrix = self._matrix()
        text = build_ai_summary(matrix)
        self.assertTrue(text)

        summary = matrix_summary(matrix['sections'], matrix['cells'])
        counters = matrix_sum(visible_cells(matrix['sections'], matrix['cells']))

        tasks_match = re.search(
            r'Задач всего: (\d+); в работе: (\d+); '
            r'просрочено: (\d+); завершено: (\d+)\.',
            text,
        )
        self.assertIsNotNone(tasks_match, text)
        total, in_progress, overdue, done = map(int, tasks_match.groups())
        self.assertEqual(total, counters['total'])
        self.assertEqual(total, summary['total_tasks'])
        self.assertEqual(in_progress, counters['in_progress'])
        self.assertEqual(overdue, counters['overdue'])
        self.assertEqual(overdue, summary['overdue_tasks'])
        self.assertEqual(done, counters['done'])
        self.assertEqual(done, summary['done_tasks'])

        pairs_match = re.search(
            r'Пар с заданиями: (\d+); пар с просрочкой: (\d+); '
            r'пар в доработке или паузе: (\d+)\.',
            text,
        )
        self.assertIsNotNone(pairs_match, text)
        pairs, overdue_pairs, rework_wait = map(int, pairs_match.groups())
        self.assertEqual(pairs, len(visible_cells(
            matrix['sections'], matrix['cells'],
        )))
        self.assertEqual(overdue_pairs, summary['overdue_pairs'])
        self.assertEqual(rework_wait, summary['rework_wait_pairs'])

        # COMMON и диагональ не попали в универсум сводки: ни одной
        # пары с участием COMMON, диагональная пара не перечислена.
        self.assertNotIn('COMMON (Общие)', text)
        self.assertNotIn('AR (Архитектура) — AR (Архитектура)', text)

    def test_status_counts_cover_seven_statuses(self):
        text = build_ai_summary(self._matrix())
        statuses_line = re.search(r'Статусы пар: (.+)\.', text)
        self.assertIsNotNone(statuses_line, text)
        counts = {}
        for piece in statuses_line.group(1).split(', '):
            match = re.match(r'^(.+) (\d+)$', piece)
            self.assertIsNotNone(match, piece)
            counts[match.group(1)] = int(match.group(2))
        self.assertEqual(counts.get('Готово'), 1)
        self.assertEqual(counts.get('Просрочено'), 1)
        self.assertEqual(counts.get('Доработка'), 1)
        self.assertEqual(counts.get('Пауза'), 1)
        self.assertEqual(counts.get('На проверке'), 1)
        self.assertEqual(counts.get('В работе'), 1)
        self.assertEqual(counts.get('Не начато'), 0)

    def test_overdue_pairs_listed_with_codes(self):
        text = build_ai_summary(self._matrix())
        self.assertIn('AR (Архитектура) — KR (Конструкции)', text)
        self.assertIn('просрочено 1 из 2', text)

    def test_deterministic_output(self):
        self.assertEqual(
            build_ai_summary(self._matrix()),
            build_ai_summary(self._matrix()),
        )

    def test_empty_project_returns_empty_string(self):
        empty = Company.objects.create(name='Пустая компания')
        gip = User.objects.create_user('gip_empty_summary', password='pass')
        UserProfile.objects.update_or_create(
            user=gip, defaults={'role': 'gip', 'company': empty},
        )
        project = Project.objects.create(
            company=empty,
            code='SM-0',
            name='Пустой проект',
            status='active',
            created_by=gip,
            gip=gip,
        )
        Section.objects.create(project=project, code='AR', name='АР', order=1)
        text = build_ai_summary(tasks_matrix_for_project(project))
        self.assertEqual(text, '')


class MatrixExportViewTests(TestCase):
    """Выгрузка XLSX/PDF (решения 3–4): гейт, format-валидация, содержимое."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания Экспорт')
        self.other_company = Company.objects.create(name='Чужая компания Экспорт')

        self.gip = User.objects.create_user('gip_export', password='pass')
        self._profile(self.gip, 'gip', self.company)

        self.developer = User.objects.create_user('dev_export', password='pass')
        self._profile(self.developer, 'developer', self.company)

        self.outsider = User.objects.create_user('outsider_export', password='pass')
        self._profile(self.outsider, 'developer', self.other_company)

        self.project = Project.objects.create(
            company=self.company,
            code='EX-1',
            name='Проект экспорта',
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
        DesignTask.objects.create(
            project=self.project,
            source_section=self.section_ar,
            target_section=self.section_kr,
            author=self.gip,
            title='Задание экспорта',
            description='Описание',
            status=DesignTask.Status.IN_PROGRESS,
            due_date=timezone.localdate() - timedelta(days=1),
        )
        self.url = reverse('storage:task_matrix_export', args=[self.project.pk])

    def _profile(self, user, role, company):
        UserProfile.objects.update_or_create(
            user=user, defaults={'role': role, 'company': company},
        )

    # ─── Гейт и format-валидация (решение 3) ────────────────────────────────

    def test_xlsx_ok_for_gip(self):
        self.client.force_login(self.gip)
        response = self.client.get(self.url, {'format': 'xlsx'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'application/vnd.openxmlformats-officedocument'
            '.spreadsheetml.sheet',
            response['Content-Type'],
        )
        self.assertTrue(response['Content-Disposition'].endswith('.xlsx"'))

    def test_pdf_ok_for_gip(self):
        self.client.force_login(self.gip)
        response = self.client.get(self.url, {'format': 'pdf'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        # Генерация без байтового парсинга содержимого: только сигнатура.
        content = b''.join(response.streaming_content)
        self.assertTrue(content.startswith(b'%PDF'))
        self.assertGreater(len(content), 1000)

    def test_missing_format_is_404(self):
        self.client.force_login(self.gip)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_invalid_format_is_404(self):
        self.client.force_login(self.gip)
        response = self.client.get(self.url, {'format': 'docx'})
        self.assertEqual(response.status_code, 404)

    def test_developer_gets_403(self):
        self.client.force_login(self.developer)
        response = self.client.get(self.url, {'format': 'xlsx'})
        self.assertEqual(response.status_code, 403)

    def test_outsider_gets_404(self):
        other_project = Project.objects.create(
            company=self.other_company,
            code='EX-OTH',
            name='Чужой проект экспорта',
            status='active',
            created_by=self.outsider,
            gip=self.outsider,
        )
        url = reverse('storage:task_matrix_export', args=[other_project.pk])
        self.client.force_login(self.gip)
        response = self.client.get(url, {'format': 'xlsx'})
        self.assertEqual(response.status_code, 404)

    def test_anonymous_redirects_to_login(self):
        response = self.client.get(self.url, {'format': 'xlsx'})
        self.assertEqual(response.status_code, 302)

    # ─── Содержимое XLSX (решение 4) ─────────────────────────────────────────

    def _workbook(self):
        self.client.force_login(self.gip)
        response = self.client.get(self.url, {'format': 'xlsx'})
        self.assertEqual(response.status_code, 200)
        return load_workbook(BytesIO(b''.join(response.streaming_content)))

    def test_xlsx_grid_without_common(self):
        workbook = self._workbook()
        sheet = workbook.active
        self.assertEqual(sheet.title, 'Матрица заданий')

        header_values = [
            cell.value for cell in sheet[3]
            if cell.value is not None
        ]
        joined = ' | '.join(str(value) for value in header_values)
        self.assertIn('AR — Архитектура', joined)
        self.assertIn('KR — Конструкции', joined)
        self.assertNotIn('COMMON', joined)

    def test_xlsx_cell_text_and_totals(self):
        workbook = self._workbook()
        sheet = workbook.active
        texts = {
            cell.value for row in sheet.iter_rows() for cell in row
            if cell.value is not None
        }
        joined = '\n'.join(str(text) for text in texts)
        self.assertIn('Просрочено', joined)
        self.assertIn('всего 1 · в работе 1 · просрочено 1 · завершено 0', joined)
        self.assertIn('Итог по столбцу', joined)

    def test_xlsx_summary_block_and_legend(self):
        workbook = self._workbook()
        sheet = workbook.active
        texts = [
            str(cell.value) for row in sheet.iter_rows() for cell in row
            if cell.value is not None
        ]
        joined = '\n'.join(texts)
        # Р8: термин «Сводка», слово «дашборд» в новых строках отсутствует.
        self.assertIn('Сводка', joined)
        self.assertNotIn('дашборд', joined.lower())
        self.assertIn('Всего задач в парах: 1', joined)
        self.assertIn('Просрочено задач: 1', joined)
        self.assertIn('Пар с просрочкой: 1', joined)
        self.assertIn('Пар в доработке или паузе: 0', joined)
        self.assertIn('Завершено задач: 0', joined)
        self.assertIn('Не начато — задания не начаты', joined)
        self.assertIn('Готово — все задания пары закрыты', joined)
