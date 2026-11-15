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
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from issues.models import Notification
from storage.domain.models import (
    Company,
    DesignTask,
    DesignTaskAttachment,
    DesignTaskChecklistItem,
    DesignTaskStatusLog,
    Document,
    Project,
    ProjectMembership,
    Section,
    TaskTemplate,
    UserProfile,
)
from storage.domain.services import add_task_attachment, create_task, issue_task, update_task_status

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
        # ГИП видит кнопку «Выдать задание».
        response = self._get_tasks_tab(self.gip)
        self.assertTrue(response.context['can_issue_task'])
        self.assertContains(response, 'Выдать задание')

        # Developer без роли ГИП/админ — не видит (ролевой гейт).
        response = self._get_tasks_tab(self.author)
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
        self.client.force_login(self.gip)
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Раздел-источник')
        self.assertContains(response, 'Раздел-цель')
        self.assertContains(response, 'Сохранить черновик')
        self.assertContains(response, 'Выдать')
        writable = list(response.context['writable_sections'])
        all_sections = list(response.context['all_sections'])
        # ГИП видит все разделы проекта.
        self.assertIn(self.section_ar, writable)
        self.assertIn(self.section_kr, writable)
        # target — все разделы проекта.
        self.assertIn(self.section_ar, all_sections)
        self.assertIn(self.section_kr, all_sections)
        # Исполнители — участники проекта.
        self.assertIn(self.assignee, response.context['assignee_choices'])

    def test_create_form_get_without_issue_rights(self):
        self.client.force_login(self.no_write_dev)
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('tab=tasks', response['Location'])
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'нет прав на выдачу заданий')

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


# ============================================================================
# Этап 4 (спека 4.3): карточка задания
# ============================================================================


class TaskCardTestBase(DesignTaskViewsTestBase):
    """Общие хелперы карточки: URL и доведение задания до статуса."""

    def _card_url(self, task=None):
        task = task or getattr(self, 'task', None)
        return reverse('storage:task_detail', args=[self.project.pk, task.pk])

    def _status_url(self, task=None):
        task = task or getattr(self, 'task', None)
        return reverse('storage:task_status', args=[self.project.pk, task.pk])

    def _attachments_url(self, task=None):
        task = task or getattr(self, 'task', None)
        return reverse(
            'storage:task_attachment', args=[self.project.pk, task.pk],
        )

    def _checklist_url(self, task=None):
        task = task or getattr(self, 'task', None)
        return reverse(
            'storage:task_checklist', args=[self.project.pk, task.pk],
        )

    def _task_issued(self, **kwargs):
        params = dict(assignee=self.assignee)
        params.update(kwargs)
        task = self._make_task(**params)
        issue_task(self.author, task)
        return task

    def _task_in_progress(self, **kwargs):
        task = self._task_issued(**kwargs)
        update_task_status(self.assignee, task, DesignTask.Status.IN_PROGRESS)
        return task

    def _task_with_checklist(self):
        # TaskTemplate уникален по (company, title) — берём готовый,
        # чтобы несколько заданий могли ссылаться на один шаблон.
        template, _ = TaskTemplate.objects.get_or_create(
            company=self.company,
            title='Шаблон карточки',
            defaults={
                'description': 'Описание',
                'default_duration_days': 5,
                'checklist': ['Проверить нагрузки', 'Сделать расчёт'],
            },
        )
        return self._task_in_progress(template=template)


class TaskDetailViewTests(TaskCardTestBase):
    """GET карточки: доступ, блоки, история, дедлайн, кнопки по правам."""

    def test_card_requires_login(self):
        task = self._task_issued()
        response = self.client.get(self._card_url(task))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_card_no_project_access(self):
        task = self._task_issued()
        self.client.force_login(self.outsider)
        response = self.client.get(self._card_url(task))
        self.assertEqual(response.status_code, 404)

    def test_task_of_other_project_404(self):
        """Задание другого проекта не открывается через URL этого проекта."""
        other_project = Project.objects.create(
            company=self.company,
            code='UI-3',
            name='Другой проект карточек',
            status='active',
            created_by=self.gip,
            gip=self.gip,
        )
        other_section = Section.objects.create(
            project=other_project, code='OV', name='ОВ', order=1,
        )
        self.author.userprofile.allowed_sections.add(other_section)
        other_task = create_task(
            self.author, other_project, other_section, other_section,
            title='Чужое задание', description='Не открывается отсюда',
        )
        self.client.force_login(self.gip)
        response = self.client.get(
            reverse(
                'storage:task_detail',
                args=[self.project.pk, other_task.pk],
            ),
        )
        self.assertEqual(response.status_code, 404)

    def test_card_shows_all_blocks(self):
        """Шапка, «Суть», разделы, «История», «Реквизиты» — у ГИПа."""
        task = self._task_in_progress()
        self.client.force_login(self.gip)
        response = self.client.get(self._card_url(task))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Передать нагрузки')
        self.assertContains(response, 'Учесть нагрузки от АР в расчёте КР')
        for block in ('Суть', 'Входные данные', 'Результат', 'История',
                      'Действия', 'Реквизиты'):
            self.assertContains(response, block)
        self.assertContains(response, self.section_ar.code)
        self.assertContains(response, self.section_kr.code)
        # Статус-бейдж — тот же класс TASK_STATUS_BADGES, что в списке.
        self.assertContains(response, 'text-bg-info')
        self.assertContains(response, 'В работе')

    def test_card_history_displayed(self):
        """Таймлайн: переходы, автор изменения, комментарий."""
        task = self._task_issued()
        update_task_status(self.assignee, task, DesignTask.Status.IN_PROGRESS)
        update_task_status(
            self.assignee, task, DesignTask.Status.ON_HOLD,
            comment='Жду исходные данные по нагрузкам',
        )
        self.client.force_login(self.author)
        response = self.client.get(self._card_url(task))
        self.assertContains(response, 'Выдано')
        self.assertContains(response, 'Приостановлено')
        self.assertContains(response, 'Жду исходные данные по нагрузкам')
        logs = response.context['status_logs']
        self.assertEqual(len(logs), 3)
        self.assertEqual(logs[0].old_status, 'DRAFT')
        self.assertEqual(logs[0].new_status, 'ISSUED')
        self.assertEqual(logs[1].new_status, 'IN_PROGRESS')
        self.assertEqual(logs[2].new_status, 'ON_HOLD')

    def test_card_history_empty_for_draft(self):
        task = self._make_task()
        self.client.force_login(self.author)
        response = self.client.get(self._card_url(task))
        self.assertContains(response, 'История пуста')

    def test_card_deadline_labels(self):
        """Живой дедлайн: просрочка, «осталось», терминальный статус."""
        today = timezone.localdate()
        overdue = self._task_in_progress(
            title='Просроченное', due_date=today - timedelta(days=2),
        )
        ahead = self._task_issued(
            title='Со сроком', due_date=today + timedelta(days=10),
        )
        today_task = self._task_issued(
            title='Срок сегодня', due_date=today,
        )

        self.client.force_login(self.gip)

        response = self.client.get(self._card_url(overdue))
        deadline = response.context['deadline']
        self.assertTrue(deadline['label'].startswith('просрочено на 2'))
        self.assertEqual(deadline['badge'], 'text-bg-danger')
        self.assertContains(response, 'просрочено на 2')

        response = self.client.get(self._card_url(ahead))
        deadline = response.context['deadline']
        self.assertEqual(deadline['label'], 'осталось 10 дней')
        self.assertEqual(deadline['badge'], 'text-bg-secondary')

        response = self.client.get(self._card_url(today_task))
        self.assertEqual(response.context['deadline']['label'], 'срок сегодня')

    def test_card_deadline_hidden_for_accepted(self):
        today = timezone.localdate()
        task = self._task_issued(due_date=today + timedelta(days=3))
        update_task_status(self.assignee, task, DesignTask.Status.IN_PROGRESS)
        add_task_attachment(
            self.assignee, task, 'output',
            file=SimpleUploadedFile('result.pdf', b'result'),
        )
        update_task_status(self.assignee, task, DesignTask.Status.COMPLETED)
        update_task_status(self.gip, task, DesignTask.Status.REVIEW)
        update_task_status(self.gip, task, DesignTask.Status.ACCEPTED)

        self.client.force_login(self.gip)
        response = self.client.get(self._card_url(task))
        self.assertIsNone(response.context['deadline'])

    def test_card_links_from_tasks_tab(self):
        """Заголовок строки во вкладке — ссылка на карточку (спека 4.3)."""
        task = self._task_issued()
        self.client.force_login(self.gip)
        response = self.client.get(
            self.project_detail_url, {'tab': 'tasks'},
        )
        self.assertContains(
            response,
            reverse('storage:task_detail', args=[self.project.pk, task.pk]),
        )

    def test_card_action_buttons_by_role(self):
        """Кнопки панели: assignee видит «Принять в работу», автор — отмену."""
        task = self._task_issued()
        self.client.force_login(self.assignee)
        response = self.client.get(self._card_url(task))
        self.assertContains(response, 'Принять в работу')
        self.assertFalse(response.context['can_cancel'])

        self.client.force_login(self.author)
        response = self.client.get(self._card_url(task))
        self.assertNotContains(response, 'Принять в работу')
        self.assertTrue(response.context['can_cancel'])
        self.assertContains(response, 'Отменить')

    def test_card_review_buttons_for_author_and_gip(self):
        task = self._task_in_progress()
        add_task_attachment(
            self.assignee, task, 'output',
            file=SimpleUploadedFile('result.pdf', b'result'),
        )
        update_task_status(self.assignee, task, DesignTask.Status.COMPLETED)
        update_task_status(self.author, task, DesignTask.Status.REVIEW)

        for user in (self.author, self.gip):
            self.client.force_login(user)
            response = self.client.get(self._card_url(task))
            self.assertTrue(response.context['can_review'])
            self.assertContains(response, 'Принять')
            self.assertContains(response, 'Вернуть на доработку')

        self.client.force_login(self.assignee)
        response = self.client.get(self._card_url(task))
        self.assertFalse(response.context['can_review'])
        self.assertNotContains(response, 'Вернуть на доработку')

    def test_card_complete_hint_without_output(self):
        """Исполнитель видит подсказку о необходимости output-вложения."""
        task = self._task_in_progress()
        self.client.force_login(self.assignee)
        response = self.client.get(self._card_url(task))
        self.assertTrue(response.context['can_complete'])
        self.assertContains(response, 'приложите результат')

    def test_card_attachment_forms_by_role(self):
        """input-форма — автору, output-форма — исполнителю в работе."""
        task = self._task_in_progress()

        self.client.force_login(self.author)
        response = self.client.get(self._card_url(task))
        self.assertTrue(response.context['can_add_input'])
        self.assertFalse(response.context['can_add_output'])
        self.assertContains(response, 'Добавить входные данные')
        self.assertNotContains(response, 'Добавить результат')

        self.client.force_login(self.assignee)
        response = self.client.get(self._card_url(task))
        self.assertFalse(response.context['can_add_input'])
        self.assertTrue(response.context['can_add_output'])
        self.assertContains(response, 'Добавить результат')
        self.assertNotContains(response, 'Добавить входные данные')

    def test_card_attachment_document_link(self):
        """Входные данные с привязкой к документу проекта — ссылкой."""
        task = self._task_in_progress()
        document = Document.objects.create(
            project=self.project,
            section=self.section_ar,
            code='AR-01',
            name='Задание на проектирование',
            doc_type='source_data',
            status='active',
            created_by=self.author,
        )
        add_task_attachment(
            self.author, task, 'input', document=document,
        )
        self.client.force_login(self.gip)
        response = self.client.get(self._card_url(task))
        self.assertEqual(response.context['input_attachments'][0].document, document)
        self.assertContains(
            response,
            reverse(
                'storage:document_detail', args=[document.pk],
            ),
        )
        self.assertContains(response, 'AR-01')

    def test_card_checklist_shown_not_toggleable_for_viewer(self):
        """Наблюдатель видит пункты, но toggle-форм у него нет."""
        task = self._task_with_checklist()
        self.client.force_login(self.viewer)
        response = self.client.get(self._card_url(task))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['checklist_total'], 2)
        self.assertEqual(response.context['checklist_done'], 0)
        self.assertFalse(response.context['can_toggle_checklist'])
        self.assertNotContains(response, self._checklist_url(task))
        self.assertContains(response, 'Проверить нагрузки')

    def test_card_contractor_sees_card(self):
        """Подрядчик-исполнитель работает с карточкой (регресс 3c95380)."""
        task = self._task_issued(assignee=self.contractor)
        self.client.force_login(self.contractor)
        response = self.client.get(self._card_url(task))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_start_work'])

    def test_card_deadline_polling_partial(self):
        """HTMX-поллинг дедлайна: ?partial=deadline отдаёт слот бейджа."""
        task = self._task_issued(
            due_date=timezone.localdate() - timedelta(days=2),
        )
        self.client.force_login(self.gip)
        response = self.client.get(
            self._card_url(task), {'partial': 'deadline'},
        )
        self.assertEqual(response.status_code, 200)
        # Слот поллинга: атрибуты HTMX с разумным TTL (60 секунд).
        self.assertContains(response, 'id="taskDeadlineSlot"')
        self.assertContains(response, 'hx-trigger="every 60s"')
        self.assertContains(response, 'hx-swap="outerHTML"')
        # Просрочка — красный бейдж с иконкой (критерий UI/UX Этапа 4).
        self.assertContains(response, 'text-bg-danger')
        self.assertContains(response, 'bi-alarm-fill')
        self.assertContains(response, 'просрочено на 2 дня')

    def test_card_deadline_polling_partial_requires_project_access(self):
        """Поллинг-фрагмент защищён так же, как карточка (чужой → 404)."""
        task = self._task_issued()
        self.client.force_login(self.outsider)
        response = self.client.get(
            self._card_url(task), {'partial': 'deadline'},
        )
        self.assertEqual(response.status_code, 404)

    def test_card_deadline_polling_partial_without_deadline(self):
        """Без срока — слот пуст, но поллинг сохраняется."""
        task = self._task_issued()
        self.client.force_login(self.gip)
        response = self.client.get(
            self._card_url(task), {'partial': 'deadline'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="taskDeadlineSlot"')
        self.assertNotContains(response, 'task-deadline')

    def test_document_detail_related_tasks(self):
        """Спека 7: карточка документа показывает «Связанные задания»."""
        task = self._task_in_progress()
        document = Document.objects.create(
            project=self.project,
            section=self.section_ar,
            code='AR-03',
            name='Пояснительная записка',
            doc_type='source_data',
            status='active',
            created_by=self.author,
        )
        add_task_attachment(
            self.author, task, DesignTaskAttachment.Kind.INPUT,
            document=document,
        )
        self.client.force_login(self.gip)
        response = self.client.get(
            reverse('storage:document_detail', args=[document.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Связанные задания')
        self.assertContains(response, 'Передать нагрузки')
        # Ссылка ведёт на карточку задания.
        self.assertContains(response, self._card_url(task))

    def test_document_detail_without_related_tasks(self):
        """У документа без привязанных заданий блока нет."""
        document = Document.objects.create(
            project=self.project,
            section=self.section_ar,
            code='AR-04',
            name='Документ без заданий',
            doc_type='other',
            status='active',
            created_by=self.author,
        )
        self.client.force_login(self.gip)
        response = self.client.get(
            reverse('storage:document_detail', args=[document.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Связанные задания')


class TaskStatusActionViewTests(TaskCardTestBase):
    """POST-смена статуса: матрица остаётся в update_task_status."""

    def test_start_work_by_assignee(self):
        task = self._task_issued()
        self.client.force_login(self.assignee)
        response = self.client.post(
            self._status_url(task), {'new_status': 'IN_PROGRESS'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(self._card_url(task), response['Location'])
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.IN_PROGRESS)

    def test_start_work_denied_for_author(self):
        """ISSUED → IN_PROGRESS разрешён только assignee (матрица)."""
        task = self._task_issued()
        self.client.force_login(self.author)
        response = self.client.post(
            self._status_url(task), {'new_status': 'IN_PROGRESS'},
        )
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.ISSUED)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'недоступен вашей роли')

    def test_hold_requires_comment(self):
        """IN_PROGRESS → ON_HOLD без комментария отклоняет сервис."""
        task = self._task_in_progress()
        self.client.force_login(self.assignee)
        response = self.client.post(
            self._status_url(task),
            {'new_status': 'ON_HOLD', 'comment': ''},
        )
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.IN_PROGRESS)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'требует комментария')

    def test_hold_with_comment_ok(self):
        task = self._task_in_progress()
        self.client.force_login(self.assignee)
        response = self.client.post(
            self._status_url(task),
            {'new_status': 'ON_HOLD', 'comment': 'Жду нагрузки'},
        )
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.ON_HOLD)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'Приостановлено')

    def test_resume_from_hold(self):
        task = self._task_in_progress()
        update_task_status(
            self.assignee, task, DesignTask.Status.ON_HOLD, comment='Пауза',
        )
        self.client.force_login(self.assignee)
        response = self.client.post(
            self._status_url(task), {'new_status': 'IN_PROGRESS'},
        )
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.IN_PROGRESS)

    def test_complete_without_output_rejected(self):
        """COMPLETED без output-вложения — ошибка сервиса, статус прежний."""
        task = self._task_in_progress()
        self.client.force_login(self.assignee)
        response = self.client.post(
            self._status_url(task), {'new_status': 'COMPLETED'},
        )
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.IN_PROGRESS)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'приложите результат')

    def test_complete_with_output_ok(self):
        task = self._task_in_progress()
        add_task_attachment(
            self.assignee, task, 'output',
            file=SimpleUploadedFile('result.pdf', b'result'),
        )
        self.client.force_login(self.assignee)
        response = self.client.post(
            self._status_url(task), {'new_status': 'COMPLETED'},
        )
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.COMPLETED)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'Выполнено')

    def test_accept_by_gip(self):
        task = self._task_in_progress()
        add_task_attachment(
            self.assignee, task, 'output',
            file=SimpleUploadedFile('result.pdf', b'result'),
        )
        update_task_status(self.assignee, task, DesignTask.Status.COMPLETED)
        update_task_status(self.gip, task, DesignTask.Status.REVIEW)

        self.client.force_login(self.gip)
        response = self.client.post(
            self._status_url(task), {'new_status': 'ACCEPTED'},
        )
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.ACCEPTED)
        self.assertIsNotNone(task.accepted_at)

    def test_return_requires_comment(self):
        task = self._task_in_progress()
        add_task_attachment(
            self.assignee, task, 'output',
            file=SimpleUploadedFile('result.pdf', b'result'),
        )
        update_task_status(self.assignee, task, DesignTask.Status.COMPLETED)
        update_task_status(self.gip, task, DesignTask.Status.REVIEW)

        self.client.force_login(self.gip)
        response = self.client.post(
            self._status_url(task),
            {'new_status': 'RETURNED', 'comment': ''},
        )
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.REVIEW)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'требует комментария')

    def test_return_with_comment_notifies_assignee(self):
        task = self._task_in_progress()
        add_task_attachment(
            self.assignee, task, 'output',
            file=SimpleUploadedFile('result.pdf', b'result'),
        )
        update_task_status(self.assignee, task, DesignTask.Status.COMPLETED)
        update_task_status(self.gip, task, DesignTask.Status.REVIEW)

        self.client.force_login(self.gip)
        response = self.client.post(
            self._status_url(task),
            {'new_status': 'RETURNED', 'comment': 'Исправить схему'},
        )
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.RETURNED)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'Исправить схему')
        self.assertTrue(
            Notification.objects.filter(recipient=self.assignee).exists(),
        )

    def test_cancel_by_author_with_comment(self):
        task = self._task_issued()
        self.client.force_login(self.author)
        response = self.client.post(
            self._status_url(task),
            {'new_status': 'CANCELLED', 'comment': 'Раздел упразднён'},
        )
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.CANCELLED)

    def test_cancel_accepted_rejected(self):
        """Терминальные статусы (ACCEPTED) отменить нельзя."""
        task = self._task_in_progress()
        add_task_attachment(
            self.assignee, task, 'output',
            file=SimpleUploadedFile('result.pdf', b'result'),
        )
        update_task_status(self.assignee, task, DesignTask.Status.COMPLETED)
        update_task_status(self.gip, task, DesignTask.Status.REVIEW)
        update_task_status(self.gip, task, DesignTask.Status.ACCEPTED)

        self.client.force_login(self.gip)
        response = self.client.post(
            self._status_url(task),
            {'new_status': 'CANCELLED', 'comment': 'Поздно'},
        )
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.ACCEPTED)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'Нельзя отменить')

    def test_unknown_status_rejected(self):
        task = self._task_issued()
        self.client.force_login(self.assignee)
        response = self.client.post(
            self._status_url(task), {'new_status': 'FOO'},
        )
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.ISSUED)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'Неизвестный статус')

    def test_status_action_no_project_access(self):
        task = self._task_issued()
        self.client.force_login(self.outsider)
        response = self.client.post(
            self._status_url(task), {'new_status': 'IN_PROGRESS'},
        )
        self.assertEqual(response.status_code, 404)

    def test_status_action_requires_post(self):
        task = self._task_issued()
        self.client.force_login(self.gip)
        response = self.client.get(self._status_url(task))
        self.assertEqual(response.status_code, 405)
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.ISSUED)


class TaskAttachmentActionViewTests(TaskCardTestBase):
    """POST-загрузка вложений input/output (add_task_attachment)."""

    def test_output_upload_by_assignee(self):
        task = self._task_in_progress()
        self.client.force_login(self.assignee)
        response = self.client.post(
            self._attachments_url(task),
            {
                'kind': 'output',
                'document': '',
                'file': SimpleUploadedFile('result.pdf', b'result'),
            },
        )
        self.assertEqual(response.status_code, 302)
        attachment = DesignTaskAttachment.objects.get()
        self.assertEqual(attachment.task, task)
        self.assertEqual(attachment.kind, 'output')
        self.assertEqual(attachment.uploaded_by, self.assignee)
        self.assertTrue(attachment.file.name)
        self.assertIsNone(attachment.document)

    def test_output_upload_denied_for_author(self):
        """output добавляет только исполнитель (сервис)."""
        task = self._task_in_progress()
        self.client.force_login(self.author)
        response = self.client.post(
            self._attachments_url(task),
            {
                'kind': 'output',
                'document': '',
                'file': SimpleUploadedFile('bad.pdf', b'bad'),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DesignTaskAttachment.objects.count(), 0)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'только исполнитель')

    def test_output_upload_wrong_status(self):
        """output только в статусе IN_PROGRESS (сервис)."""
        task = self._task_issued()
        self.client.force_login(self.assignee)
        response = self.client.post(
            self._attachments_url(task),
            {
                'kind': 'output',
                'document': '',
                'file': SimpleUploadedFile('result.pdf', b'result'),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DesignTaskAttachment.objects.count(), 0)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'только в статусе')

    def test_input_upload_with_document(self):
        """input от автора с привязкой к документу проекта."""
        task = self._task_in_progress()
        document = Document.objects.create(
            project=self.project,
            section=self.section_ar,
            code='AR-02',
            name='Техническое задание',
            doc_type='source_data',
            status='active',
            created_by=self.author,
        )
        self.client.force_login(self.author)
        response = self.client.post(
            self._attachments_url(task),
            {'kind': 'input', 'document': str(document.pk)},
        )
        self.assertEqual(response.status_code, 302)
        attachment = DesignTaskAttachment.objects.get()
        self.assertEqual(attachment.kind, 'input')
        self.assertEqual(attachment.document, document)
        self.assertFalse(attachment.file)

    def test_input_upload_foreign_document_rejected(self):
        """Документ другого проекта в select не проходит (вьюха)."""
        other_project = Project.objects.create(
            company=self.company,
            code='UI-9',
            name='Другой проект для документов',
            status='active',
            created_by=self.gip,
            gip=self.gip,
        )
        foreign_document = Document.objects.create(
            project=other_project,
            section=Section.objects.create(
                project=other_project, code='XX', name='Чужой', order=1,
            ),
            code='XX-01',
            name='Чужой документ',
            doc_type='other',
            status='active',
            created_by=self.gip,
        )
        task = self._task_in_progress()
        self.client.force_login(self.author)
        response = self.client.post(
            self._attachments_url(task),
            {'kind': 'input', 'document': str(foreign_document.pk)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DesignTaskAttachment.objects.count(), 0)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'не найден в этом проекте')

    def test_attachment_requires_file_or_document(self):
        """Ни файла, ни документа — сервис требует одно из двух."""
        task = self._task_in_progress()
        self.client.force_login(self.author)
        response = self.client.post(
            self._attachments_url(task), {'kind': 'input', 'document': ''},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DesignTaskAttachment.objects.count(), 0)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'требует документ или файл')

    def test_attachment_no_project_access(self):
        task = self._task_in_progress()
        self.client.force_login(self.outsider)
        response = self.client.post(
            self._attachments_url(task),
            {
                'kind': 'output',
                'file': SimpleUploadedFile('x.pdf', b'x'),
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_attachment_requires_post(self):
        task = self._task_in_progress()
        self.client.force_login(self.author)
        response = self.client.get(self._attachments_url(task))
        self.assertEqual(response.status_code, 405)

    def test_output_upload_htmx_fragment(self):
        """HTMX-загрузка: фрагмент карточки вместо redirect (мгновенный UI)."""
        task = self._task_in_progress()
        self.client.force_login(self.assignee)
        response = self.client.post(
            self._attachments_url(task),
            {
                'kind': 'output',
                'document': '',
                'file': SimpleUploadedFile('result.pdf', b'result'),
            },
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)
        # Фрагмент — карточка «Результат» с приложением внутри.
        self.assertContains(response, 'id="taskOutputCard"')
        # Имя файла сохраняется (с суффиксом коллизии хранилища).
        self.assertContains(response, 'result_')
        self.assertContains(response, '.pdf')
        self.assertContains(response, '/media/design_tasks/attachments/')
        self.assertContains(response, 'Результат: вложение добавлено.')
        self.assertEqual(DesignTaskAttachment.objects.count(), 1)

    def test_attachment_file_type_restricted(self):
        """Белый список расширений — та же политика, что у документов."""
        task = self._task_in_progress()
        self.client.force_login(self.assignee)
        response = self.client.post(
            self._attachments_url(task),
            {
                'kind': 'output',
                'document': '',
                'file': SimpleUploadedFile('virus.exe', b'evil'),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DesignTaskAttachment.objects.count(), 0)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'не поддерживается')

    def test_attachment_file_type_restricted_htmx(self):
        """Отказ по расширению в HTMX-режиме — фрагмент с ошибкой."""
        task = self._task_in_progress()
        self.client.force_login(self.assignee)
        response = self.client.post(
            self._attachments_url(task),
            {
                'kind': 'output',
                'document': '',
                'file': SimpleUploadedFile('virus.exe', b'evil'),
            },
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(DesignTaskAttachment.objects.count(), 0)
        self.assertContains(response, 'id="taskOutputCard"')
        self.assertContains(response, 'не поддерживается')

    def test_attachment_file_size_restricted(self):
        """Файл больше лимита отклоняется (та же константа MAX_UPLOAD_SIZE)."""
        task = self._task_in_progress()
        self.client.force_login(self.assignee)
        with mock.patch(
            'storage.adapters.views.MAX_UPLOAD_SIZE', 10,
        ):
            response = self.client.post(
                self._attachments_url(task),
                {
                    'kind': 'output',
                    'document': '',
                    'file': SimpleUploadedFile('big.pdf', b'x' * 11),
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DesignTaskAttachment.objects.count(), 0)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'Файл больше')

    def test_attachment_kind_comment_rejected_from_card(self):
        """kind=comment — не форма карточки: вьюха допускает input/output."""
        task = self._task_in_progress()
        self.client.force_login(self.author)
        response = self.client.post(
            self._attachments_url(task),
            {
                'kind': 'comment',
                'document': '',
                'file': SimpleUploadedFile('note.pdf', b'note'),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DesignTaskAttachment.objects.count(), 0)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'Недопустимый тип вложения')


class TaskChecklistActionViewTests(TaskCardTestBase):
    """POST-переключение чек-листа (toggle_checklist_item)."""

    def setUp(self):
        super().setUp()
        self.task = self._task_with_checklist()
        self.items = list(self.task.checklist_items.order_by('id'))

    def test_toggle_by_assignee(self):
        self.client.force_login(self.assignee)
        response = self.client.post(
            self._checklist_url(), {'item_id': str(self.items[0].pk)},
        )
        self.assertEqual(response.status_code, 302)
        self.items[0].refresh_from_db()
        self.assertTrue(self.items[0].is_done)
        self.assertEqual(self.items[0].done_by, self.assignee)
        self.assertIsNotNone(self.items[0].done_at)
        # Повторное переключение снимает отметку.
        self.client.post(self._checklist_url(), {'item_id': str(self.items[0].pk)})
        self.items[0].refresh_from_db()
        self.assertFalse(self.items[0].is_done)
        self.assertIsNone(self.items[0].done_by)

    def test_toggle_by_gip_ok(self):
        self.client.force_login(self.gip)
        response = self.client.post(
            self._checklist_url(), {'item_id': str(self.items[0].pk)},
        )
        self.items[0].refresh_from_db()
        self.assertTrue(self.items[0].is_done)
        self.assertEqual(self.items[0].done_by, self.gip)

    def test_toggle_denied_for_viewer(self):
        """Наблюдатель не переключает чек-лист (сервис)."""
        self.client.force_login(self.viewer)
        response = self.client.post(
            self._checklist_url(), {'item_id': str(self.items[0].pk)},
        )
        self.assertEqual(response.status_code, 302)
        self.items[0].refresh_from_db()
        self.assertFalse(self.items[0].is_done)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'Чек-лист отмечает исполнитель')

    def test_unknown_item_rejected(self):
        self.client.force_login(self.assignee)
        response = self.client.post(
            self._checklist_url(), {'item_id': '999999'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            DesignTaskChecklistItem.objects.filter(is_done=True).count(), 0,
        )
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'не найден')

    def test_item_of_other_task_rejected(self):
        other_task = self._task_with_checklist()
        other_item = other_task.checklist_items.order_by('id').first()
        self.client.force_login(self.assignee)
        response = self.client.post(
            self._checklist_url(), {'item_id': str(other_item.pk)},
        )
        self.assertEqual(response.status_code, 302)
        other_item.refresh_from_db()
        self.assertFalse(other_item.is_done)
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'не найден')

    def test_checklist_action_requires_post(self):
        self.client.force_login(self.assignee)
        response = self.client.get(self._checklist_url())
        self.assertEqual(response.status_code, 405)

    def test_toggle_htmx_fragment_by_assignee(self):
        """HTMX toggle: фрагмент чек-листа вместо redirect (мгновенно)."""
        self.client.force_login(self.assignee)
        response = self.client.post(
            self._checklist_url(),
            {'item_id': str(self.items[0].pk)},
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)
        self.items[0].refresh_from_db()
        self.assertTrue(self.items[0].is_done)
        # Фрагмент — карточка чек-листа с обновлённым счётчиком.
        self.assertContains(response, 'id="taskChecklistCard"')
        self.assertContains(response, '1 / 2')
        self.assertContains(response, 'Пункт «Проверить нагрузки» отмечен.')

    def test_toggle_htmx_denied_for_viewer(self):
        """HTMX-отказ: фрагмент с ошибкой прав, состояние не меняется."""
        self.client.force_login(self.viewer)
        response = self.client.post(
            self._checklist_url(),
            {'item_id': str(self.items[0].pk)},
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)
        self.items[0].refresh_from_db()
        self.assertFalse(self.items[0].is_done)
        self.assertContains(response, 'id="taskChecklistCard"')
        self.assertContains(response, 'Чек-лист отмечает исполнитель')
        # Счётчик не изменился.
        self.assertContains(response, '0 / 2')
