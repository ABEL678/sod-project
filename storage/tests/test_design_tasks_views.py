"""Модуль «Задания» (DesignTask), Этап 3 — UI: вкладка «Задания» и
форма создания (спека 4.1–4.2).

Покрывает: вкладку ?tab=tasks в ProjectDetailView (сегмент-контрол
Все/Входящие/Исходящие, фильтры статус/срок/приоритет, кнопка «Выдать
задание», пустое состояние), форму /projects/<id>/tasks/new/ (GET и
POST через сервис create_task, кнопки «Сохранить черновик»/«Выдать»,
шаблон с автозаполнением срока) и POST-выдачу черновика из списка
(task_issue). Права проверяются на уровне сервисов — вьюхи тонкие.

Этап 2 — сервисы (см. storage/tests/test_design_tasks.py),
Этап 1 — модели (см. storage/domain/models.py).
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from issues.models import Notification
from storage.domain.models import (
    Company,
    DesignTask,
    DesignTaskChecklistItem,
    DesignTaskStatusLog,
    Project,
    ProjectMembership,
    Section,
    TaskTemplate,
    UserProfile,
)
from storage.domain.services import create_task

User = get_user_model()


class DesignTaskViewsTestBase(TestCase):
    """Фикстуры: компания, ГИП, автор (АР), исполнитель (КР), чужак."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания Задания UI')
        self.other_company = Company.objects.create(name='Чужая компания')

        self.gip = User.objects.create_user('gip_ui', password='pass')
        self._profile(self.gip, 'gip')

        self.author = User.objects.create_user('author_ui', password='pass')
        self._profile(self.author, 'developer')

        self.assignee = User.objects.create_user('assignee_ui', password='pass')
        self._profile(self.assignee, 'developer')

        # Разработчик без права записи в разделы проекта.
        self.no_write_dev = User.objects.create_user('nowrite_ui', password='pass')
        self._profile(self.no_write_dev, 'developer')

        # Только чтение.
        self.viewer = User.objects.create_user('viewer_ui', password='pass')
        self._profile(self.viewer, 'viewer')

        # Подрядчик — видит разделы по allowed_sections, но не управляет
        # участниками (регресс 3c95380: вкладки должны быть ему доступны).
        self.contractor = User.objects.create_user('contractor_ui', password='pass')
        self._profile(self.contractor, 'contractor')

        # Пользователь другой компании — доступа к проекту нет.
        self.outsider = User.objects.create_user('outsider_ui', password='pass')
        self._profile(self.outsider, 'developer', company=self.other_company)

        self.project = Project.objects.create(
            company=self.company,
            code='UI-1',
            name='Проект UI-заданий',
            status='active',
            created_by=self.gip,
            gip=self.gip,
        )
        self.section_ar = Section.objects.create(
            project=self.project, code='AR', name='АР', order=1,
        )
        self.section_kr = Section.objects.create(
            project=self.project, code='KR', name='КР', order=2,
        )
        # write-доступ: автор — в АР (source), исполнитель — в КР (target),
        # подрядчик — в КР (только чтение реестра разделов).
        self.author.userprofile.allowed_sections.add(self.section_ar)
        self.assignee.userprofile.allowed_sections.add(self.section_kr)
        self.contractor.userprofile.allowed_sections.add(self.section_kr)

        # Участники проекта — их видит выпадающий список «Исполнитель».
        self._membership(self.gip, 'gip')
        self._membership(self.author, 'developer')
        self._membership(self.assignee, 'developer')
        self._membership(self.viewer, 'viewer')
        self._membership(self.contractor, 'contractor')

        self.project_detail_url = reverse(
            'storage:project_detail', args=[self.project.pk],
        )
        self.create_url = reverse('storage:task_create', args=[self.project.pk])

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

    def _make_task(self, **kwargs):
        source = kwargs.pop('source_section', self.section_ar)
        target = kwargs.pop('target_section', self.section_kr)
        author = kwargs.pop('author', self.author)
        params = dict(
            title='Передать нагрузки',
            description='Учесть нагрузки от АР в расчёте КР',
        )
        params.update(kwargs)
        return create_task(author, self.project, source, target, **params)

    def _get_tasks_tab(self, user=None, query=None):
        if user is not None:
            self.client.force_login(user)
        return self.client.get(self.project_detail_url, query or {'tab': 'tasks'})


class TaskListViewTests(DesignTaskViewsTestBase):
    """Вкладка «Задания» (?tab=tasks): доступ, фильтры, сегменты, кнопки."""

    def test_tasks_tab_requires_login(self):
        response = self.client.get(self.project_detail_url, {'tab': 'tasks'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_tasks_tab_gip_default_all(self):
        task = self._make_task(assignee=self.assignee)
        response = self._get_tasks_tab(self.gip)
        self.assertEqual(response.status_code, 200)
        # По умолчанию ГИП видит «Все» задания проекта.
        self.assertEqual(response.context['active_role_filter'], 'all')
        self.assertIn(task, response.context['tasks'])
        # Сегмент-контрол: ГИПу доступны все три сегмента.
        segment_values = [s['value'] for s in response.context['task_segments']]
        self.assertEqual(segment_values, ['all', 'incoming', 'outgoing'])
        self.assertContains(response, 'Задания проекта')
        self.assertContains(response, 'Выдать задание')

    def test_tasks_tab_developer_default_incoming(self):
        task = self._make_task(assignee=self.assignee)
        response = self._get_tasks_tab(self.assignee)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_role_filter'], 'incoming')
        self.assertIn(task, response.context['tasks'])
        segment_values = [s['value'] for s in response.context['task_segments']]
        self.assertEqual(segment_values, ['incoming', 'outgoing'])
        self.assertNotContains(response, 'Сегмент «Все»')

    def test_tasks_tab_developer_outgoing_filter(self):
        task = self._make_task(assignee=self.assignee)
        response = self._get_tasks_tab(self.author, {'tab': 'tasks', 'role_filter': 'outgoing'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_role_filter'], 'outgoing')
        self.assertIn(task, response.context['tasks'])

    def test_tasks_tab_all_requested_by_developer_falls_back(self):
        """Подделанный ?role_filter=all не-ГИПом мягко откатывается."""
        task = self._make_task(assignee=self.assignee)
        response = self._get_tasks_tab(self.assignee, {'tab': 'tasks', 'role_filter': 'all'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_role_filter'], 'incoming')
        self.assertIn(task, response.context['tasks'])

    def test_tasks_tab_no_project_access(self):
        self.client.force_login(self.outsider)
        response = self.client.get(self.project_detail_url, {'tab': 'tasks'})
        self.assertEqual(response.status_code, 404)

    def test_tasks_tab_status_filter(self):
        draft = self._make_task(title='Черновик')
        issued = self._make_task(title='Выданное задание')
        from storage.domain.services import issue_task

        issue_task(self.author, issued)
        response = self._get_tasks_tab(
            self.gip, {'tab': 'tasks', 'status': 'ISSUED'},
        )
        self.assertEqual(response.status_code, 200)
        tasks = list(response.context['tasks'])
        self.assertIn(issued, tasks)
        self.assertNotIn(draft, tasks)

    def test_tasks_tab_priority_filter(self):
        normal = self._make_task(title='Обычное задание')
        critical = self._make_task(title='Критичное задание', priority='critical')
        response = self._get_tasks_tab(
            self.gip, {'tab': 'tasks', 'priority': 'critical'},
        )
        self.assertEqual(response.status_code, 200)
        tasks = list(response.context['tasks'])
        self.assertIn(critical, tasks)
        self.assertNotIn(normal, tasks)

    def test_tasks_tab_due_filters(self):
        today = timezone.localdate()
        overdue = self._make_task(
            title='Просроченное', due_date=today - timedelta(days=1),
        )
        this_week = self._make_task(
            title='Срочное на неделе', due_date=today + timedelta(days=3),
        )
        self._make_task(title='Далёкий срок', due_date=today + timedelta(days=30))

        overdue_response = self._get_tasks_tab(
            self.gip, {'tab': 'tasks', 'due': 'overdue'},
        )
        overdue_tasks = list(overdue_response.context['tasks'])
        self.assertIn(overdue, overdue_tasks)
        self.assertNotIn(this_week, overdue_tasks)

        week_response = self._get_tasks_tab(
            self.gip, {'tab': 'tasks', 'due': 'week'},
        )
        week_tasks = list(week_response.context['tasks'])
        self.assertIn(this_week, week_tasks)
        self.assertNotIn(overdue, week_tasks)

    def test_tasks_tab_overdue_row_highlighted(self):
        self._make_task(
            title='Просроченное',
            due_date=timezone.localdate() - timedelta(days=1),
        )
        response = self._get_tasks_tab(self.gip)
        self.assertContains(response, 'text-danger')

    def test_tasks_tab_issue_button_rights(self):
        # Автор (developer с write в АР) видит кнопку «Выдать задание».
        response = self._get_tasks_tab(self.author)
        self.assertTrue(response.context['can_issue_task'])
        self.assertContains(response, 'Выдать задание')

        # Наблюдатель без write-доступа — не видит.
        response = self._get_tasks_tab(self.viewer)
        self.assertFalse(response.context['can_issue_task'])
        self.assertNotContains(response, 'Выдать задание')

    def test_tasks_tab_empty_state(self):
        response = self._get_tasks_tab(self.gip)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['tasks']), [])
        self.assertContains(response, 'Заданий нет. Выдайте первое задание')
        self.assertContains(response, 'Выдайте первое задание')

    def test_tasks_tab_contractor_sees_tab_without_manage(self):
        """Регресс 3c95380: вкладки доступны и без права управления."""
        task = self._make_task(assignee=self.contractor)
        response = self._get_tasks_tab(self.contractor)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_role_filter'], 'incoming')
        self.assertIn(task, response.context['tasks'])
        self.assertFalse(response.context['can_manage_participants'])
        # Управляющие вкладки скрыты, «Задания» — видна.
        self.assertNotContains(response, '?tab=participants')
        self.assertNotContains(response, '?tab=trash')
        self.assertContains(response, '?tab=tasks')


class TaskCreateViewTests(DesignTaskViewsTestBase):
    """Форма создания задания (/projects/<id>/tasks/new/)."""

    def _valid_post(self, **overrides):
        data = {
            'source_section': str(self.section_ar.pk),
            'target_section': str(self.section_kr.pk),
            'title': 'Новое задание из формы',
            'description': 'Текст задания из формы',
            'due_date': '',
            'assignee': str(self.assignee.pk),
            'template': '',
            'priority': 'normal',
            'action': 'draft',
        }
        data.update(overrides)
        return data

    def test_create_form_get_ok(self):
        self.client.force_login(self.author)
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Раздел-источник')
        self.assertContains(response, 'Раздел-цель')
        self.assertContains(response, 'Сохранить черновик')
        self.assertContains(response, 'Выдать')
        writable = list(response.context['writable_sections'])
        all_sections = list(response.context['all_sections'])
        # source — только разделы с write-доступом (у автора — АР).
        self.assertIn(self.section_ar, writable)
        self.assertNotIn(self.section_kr, writable)
        # target — все разделы проекта.
        self.assertIn(self.section_ar, all_sections)
        self.assertIn(self.section_kr, all_sections)
        # Исполнители — участники проекта.
        self.assertIn(self.assignee, response.context['assignee_choices'])

    def test_create_form_get_without_write_access(self):
        self.client.force_login(self.no_write_dev)
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('tab=tasks', response['Location'])
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'правом записи')

    def test_create_form_get_no_project_access(self):
        self.client.force_login(self.outsider)
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 404)

    def test_create_post_draft_ok(self):
        self.client.force_login(self.author)
        self.assertEqual(DesignTask.objects.count(), 0)
        response = self.client.post(self.create_url, self._valid_post())
        self.assertEqual(response.status_code, 302)
        self.assertIn('tab=tasks', response['Location'])
        task = DesignTask.objects.get()
        self.assertEqual(task.status, DesignTask.Status.DRAFT)
        self.assertEqual(task.author, self.author)
        self.assertEqual(task.assignee, self.assignee)
        self.assertEqual(task.source_section, self.section_ar)
        self.assertEqual(task.target_section, self.section_kr)
        self.assertEqual(task.priority, 'normal')
        self.assertIsNone(task.due_date)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'сохранено как черновик')

    def test_create_post_issue_ok(self):
        self.client.force_login(self.author)
        response = self.client.post(
            self.create_url, self._valid_post(action='issue'),
        )
        self.assertEqual(response.status_code, 302)
        task = DesignTask.objects.get()
        self.assertEqual(task.status, DesignTask.Status.ISSUED)
        self.assertIsNotNone(task.issued_at)
        # Выдача: лог перехода и уведомление исполнителю.
        self.assertTrue(
            DesignTaskStatusLog.objects.filter(
                task=task, old_status='DRAFT', new_status='ISSUED',
            ).exists(),
        )
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.assignee,
                message__contains=f'#{task.pk}',
            ).exists(),
        )
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'выдано')

    def test_create_post_with_template(self):
        template = TaskTemplate.objects.create(
            company=self.company,
            title='Шаблон UI',
            description='Типовой текст',
            default_duration_days=10,
            checklist=['Пункт 1', 'Пункт 2'],
        )
        self.client.force_login(self.author)
        response = self.client.post(
            self.create_url,
            self._valid_post(template=str(template.pk), assignee=''),
        )
        self.assertEqual(response.status_code, 302)
        task = DesignTask.objects.get()
        self.assertEqual(task.template, template)
        # Срок не указан — подставлен из default_duration_days.
        self.assertEqual(
            task.due_date,
            timezone.localdate() + timedelta(days=10),
        )
        # Чек-лист шаблона скопирован в задание.
        self.assertEqual(
            list(
                DesignTaskChecklistItem.objects.filter(task=task)
                .values_list('text', flat=True)
            ),
            ['Пункт 1', 'Пункт 2'],
        )

    def test_create_post_missing_title(self):
        self.client.force_login(self.author)
        response = self.client.post(
            self.create_url, self._valid_post(title=''),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DesignTask.objects.count(), 0)
        self.assertContains(response, 'Укажите краткое название', status_code=400)

    def test_create_post_assignee_not_member(self):
        self.client.force_login(self.author)
        response = self.client.post(
            self.create_url,
            self._valid_post(assignee=str(self.outsider.pk)),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DesignTask.objects.count(), 0)
        self.assertContains(
            response, 'Исполнитель должен быть участником проекта',
            status_code=400,
        )

    def test_create_post_foreign_section(self):
        other_project = Project.objects.create(
            company=self.company,
            code='UI-2',
            name='Другой проект',
            status='active',
            created_by=self.gip,
            gip=self.gip,
        )
        foreign_section = Section.objects.create(
            project=other_project, code='XX', name='Чужой раздел', order=1,
        )
        self.client.force_login(self.author)
        response = self.client.post(
            self.create_url,
            self._valid_post(source_section=str(foreign_section.pk)),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DesignTask.objects.count(), 0)
        self.assertContains(response, 'Выберите раздел-источник', status_code=400)

    def test_create_post_service_permission_denied(self):
        """POST напрямую без write-доступа — сервис create_task отклоняет."""
        self.client.force_login(self.viewer)
        response = self.client.post(self.create_url, self._valid_post())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DesignTask.objects.count(), 0)
        self.assertContains(
            response, 'Нет прав на выдачу задания', status_code=400,
        )

    def test_create_post_foreign_template(self):
        foreign_template = TaskTemplate.objects.create(
            company=self.other_company,
            title='Чужой шаблон',
            description='Описание',
            default_duration_days=3,
            checklist=[],
        )
        self.client.force_login(self.author)
        response = self.client.post(
            self.create_url,
            self._valid_post(template=str(foreign_template.pk)),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DesignTask.objects.count(), 0)
        self.assertContains(response, 'Шаблон не найден', status_code=400)


class TaskIssueActionViewTests(DesignTaskViewsTestBase):
    """POST-выдача черновика из списка (колонка «Действия»)."""

    def setUp(self):
        super().setUp()
        self.task = self._make_task(assignee=self.assignee)
        self.issue_url = reverse(
            'storage:task_issue', args=[self.project.pk, self.task.pk],
        )

    def test_issue_action_by_gip(self):
        self.client.force_login(self.gip)
        response = self.client.post(self.issue_url)
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, DesignTask.Status.ISSUED)
        self.assertIsNotNone(self.task.issued_at)
        self.assertTrue(
            Notification.objects.filter(recipient=self.assignee).exists(),
        )

    def test_issue_action_by_author(self):
        self.client.force_login(self.author)
        response = self.client.post(self.issue_url)
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, DesignTask.Status.ISSUED)

    def test_issue_action_denied_for_stranger(self):
        """Разработчик без отношения к заданию выдать не может."""
        self.client.force_login(self.no_write_dev)
        response = self.client.post(self.issue_url)
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, DesignTask.Status.DRAFT)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'может автор')

    def test_issue_action_wrong_status(self):
        from storage.domain.services import issue_task as service_issue

        service_issue(self.author, self.task)
        self.client.force_login(self.author)
        response = self.client.post(self.issue_url)
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, DesignTask.Status.ISSUED)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'DRAFT')

    def test_issue_action_no_project_access(self):
        self.client.force_login(self.outsider)
        response = self.client.post(self.issue_url)
        self.assertEqual(response.status_code, 404)

    def test_issue_action_requires_post(self):
        self.client.force_login(self.gip)
        response = self.client.get(self.issue_url)
        self.assertEqual(response.status_code, 405)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, DesignTask.Status.DRAFT)
