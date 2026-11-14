"""Модуль «Задания» (DesignTask), Этап 2 — сервисы (спека 3).

Покрывает: создание/выдачу заданий, матрицу переходов статусов
update_task_status, права по ролям (assignee/author/gip/admin),
вложения (input/output/comment), чек-лист, сводку ГИПа и фильтры списка.
Модели — Этап 1 (см. storage/domain/models.py), UI — Этапы 3–4.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from issues.models import Notification
from storage.domain.models import (
    Company,
    DesignTask,
    DesignTaskAttachment,
    DesignTaskChecklistItem,
    DesignTaskStatusLog,
    Project,
    Section,
    TaskTemplate,
    UserProfile,
)
from storage.domain.services import (
    add_task_attachment,
    create_task,
    issue_task,
    list_tasks,
    task_summary_for_gip,
    toggle_checklist_item,
    update_task_status,
)

User = get_user_model()


class DesignTaskTestBase(TestCase):
    """Фикстуры: компания, ГИП, автор (АР), исполнитель (КР), чужак."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания Задания')

        self.gip = User.objects.create_user('gip_dt', password='pass')
        self._profile(self.gip, 'gip')

        self.author = User.objects.create_user('author_dt', password='pass')
        self._profile(self.author, 'developer')

        self.assignee = User.objects.create_user('assignee_dt', password='pass')
        self._profile(self.assignee, 'developer')

        self.outsider = User.objects.create_user('outsider_dt', password='pass')
        self._profile(self.outsider, 'developer')

        self.admin = User.objects.create_superuser('admin_dt', 'a@a.ru', 'pass')

        self.project = Project.objects.create(
            company=self.company,
            code='DT-1',
            name='Проект заданий',
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
        # write-доступ: автор — в АР (source), исполнитель — в КР (target)
        self.author.userprofile.allowed_sections.add(self.section_ar)
        self.assignee.userprofile.allowed_sections.add(self.section_kr)

    def _profile(self, user, role):
        UserProfile.objects.update_or_create(
            user=user, defaults={'role': role, 'company': self.company},
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

    def _make_template(self):
        return TaskTemplate.objects.create(
            company=self.company,
            title='Шаблон АР→КР',
            description='Типовое задание на учёт нагрузок',
            default_duration_days=5,
            checklist=['Проверить нагрузки', 'Сделать расчёт', 'Согласовать с ГИПом'],
        )

    def _output_attachment(self, task):
        """Результат от исполнителя (задание должно быть IN_PROGRESS)."""
        return add_task_attachment(
            self.assignee, task, 'output',
            file=SimpleUploadedFile('result.pdf', b'result'),
        )

    def _task_in_progress(self):
        task = self._make_task(assignee=self.assignee)
        issue_task(self.author, task)
        update_task_status(self.assignee, task, DesignTask.Status.IN_PROGRESS)
        return task


class CreateTaskTests(DesignTaskTestBase):
    """create_task: права, валидация, шаблон и расчёт срока."""

    def test_create_task_ok(self):
        task = self._make_task()
        self.assertEqual(task.status, DesignTask.Status.DRAFT)
        self.assertEqual(task.priority, 'normal')
        self.assertEqual(task.author, self.author)
        self.assertIsNone(task.due_date)
        self.assertFalse(
            DesignTaskChecklistItem.objects.filter(task=task).exists()
        )

    def test_create_task_wrong_section(self):
        other_project = Project.objects.create(
            company=self.company,
            code='DT-2',
            name='Чужой проект',
            status='active',
            created_by=self.gip,
        )
        foreign_section = Section.objects.create(
            project=other_project, code='OV', name='ОВ', order=1,
        )
        with self.assertRaises(ValueError):
            self._make_task(target_section=foreign_section)
        with self.assertRaises(ValueError):
            self._make_task(source_section=foreign_section)

    def test_create_task_no_permission(self):
        # Чужак без write-доступа к АР не может выдать задание от АР
        with self.assertRaises(PermissionDenied):
            create_task(
                self.outsider, self.project,
                self.section_ar, self.section_kr,
                title='Задание', description='…',
            )
        self.assertFalse(DesignTask.objects.exists())

    def test_create_task_with_template(self):
        template = self._make_template()
        task = self._make_task(template=template)
        self.assertEqual(task.template, template)
        # Чек-лист шаблона скопирован в пункты задания
        items = list(
            DesignTaskChecklistItem.objects.filter(task=task)
            .order_by('id').values_list('text', flat=True)
        )
        self.assertEqual(items, template.checklist)
        # Срок из default_duration_days, раз due_date не задан
        self.assertEqual(
            task.due_date,
            timezone.localdate() + timedelta(days=5),
        )
        # Шаблон по-прежнему с исходным чек-листом (копия, не ссылка)
        template.checklist.append('Лишний пункт')
        self.assertNotIn(
            'Лишний пункт',
            list(DesignTaskChecklistItem.objects.filter(task=task)
                 .values_list('text', flat=True)),
        )

    def test_create_task_explicit_due_date_wins(self):
        template = self._make_template()
        fixed = timezone.localdate() + timedelta(days=30)
        task = self._make_task(template=template, due_date=fixed)
        self.assertEqual(task.due_date, fixed)


class IssueTaskTests(DesignTaskTestBase):
    """issue_task: статус, issued_at, лог, уведомление исполнителя."""

    def test_issue_task_ok(self):
        task = self._make_task(assignee=self.assignee)
        result = issue_task(self.author, task)
        self.assertEqual(result.status, DesignTask.Status.ISSUED)
        self.assertIsNotNone(result.issued_at)
        self.assertTrue(
            DesignTaskStatusLog.objects.filter(
                task=task,
                old_status=DesignTask.Status.DRAFT,
                new_status=DesignTask.Status.ISSUED,
                changed_by=self.author,
            ).exists()
        )
        notification = Notification.objects.filter(
            recipient=self.assignee, verb='assigned',
        ).first()
        self.assertIsNotNone(notification)
        self.assertIn('Вам выдано задание', notification.message)
        self.assertIn(
            f'/projects/{self.project.pk}/tasks/{task.pk}/',
            notification.url,
        )

    def test_issue_task_by_gip(self):
        task = self._make_task()
        issue_task(self.gip, task)
        self.assertEqual(task.status, DesignTask.Status.ISSUED)

    def test_issue_task_wrong_status(self):
        task = self._make_task(assignee=self.assignee)
        issue_task(self.author, task)
        with self.assertRaises(ValueError):
            issue_task(self.author, task)

    def test_issue_task_no_permission(self):
        task = self._make_task(assignee=self.assignee)
        with self.assertRaises(PermissionDenied):
            issue_task(self.outsider, task)
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.DRAFT)


class UpdateTaskStatusTests(DesignTaskTestBase):
    """update_task_status: матрица переходов, права, условия, лог."""

    def _allowed_chain(self, task, actor_assignee=None, actor_author=None):
        """Проводит задание по разрешённой цепочке до ACCEPTED."""
        actor_assignee = actor_assignee or self.assignee
        actor_author = actor_author or self.author
        update_task_status(actor_assignee, task, DesignTask.Status.IN_PROGRESS)
        self._output_attachment(task)
        update_task_status(actor_assignee, task, DesignTask.Status.COMPLETED)
        update_task_status(actor_author, task, DesignTask.Status.REVIEW)
        update_task_status(actor_author, task, DesignTask.Status.ACCEPTED)
        task.refresh_from_db()
        return task

    def test_update_status_matrix_happy_path(self):
        task = self._make_task(assignee=self.assignee)
        issue_task(self.author, task)
        self._allowed_chain(task)
        self.assertEqual(task.status, DesignTask.Status.ACCEPTED)
        self.assertIsNotNone(task.accepted_at)
        self.assertEqual(task.closed_by, self.author)
        statuses = list(
            DesignTaskStatusLog.objects.filter(task=task)
            .values_list('new_status', flat=True)
        )
        self.assertEqual(
            statuses,
            [
                DesignTask.Status.ISSUED,
                DesignTask.Status.IN_PROGRESS,
                DesignTask.Status.COMPLETED,
                DesignTask.Status.REVIEW,
                DesignTask.Status.ACCEPTED,
            ],
        )

    def test_update_status_matrix_forbidden(self):
        task = self._make_task(assignee=self.assignee)

        with self.subTest('DRAFT→ISSUED через update_task_status'):
            with self.assertRaises(ValidationError):
                update_task_status(self.author, task, DesignTask.Status.ISSUED)

        issue_task(self.author, task)
        # Автор — не исполнитель: переход исполнителя ему недоступен
        with self.subTest('автор не может ISSUED→IN_PROGRESS'):
            with self.assertRaises(PermissionDenied):
                update_task_status(self.author, task, DesignTask.Status.IN_PROGRESS)

        update_task_status(self.assignee, task, DesignTask.Status.IN_PROGRESS)
        with self.subTest('ISSUED пропущен: IN_PROGRESS→REVIEW'):
            with self.assertRaises(ValidationError):
                update_task_status(self.assignee, task, DesignTask.Status.REVIEW)
        with self.subTest('неизвестный статус'):
            with self.assertRaises(ValueError):
                update_task_status(self.assignee, task, 'UNKNOWN')
        with self.subTest('та же статус-строка'):
            with self.assertRaises(ValueError):
                update_task_status(
                    self.assignee, task, DesignTask.Status.IN_PROGRESS,
                )

    def test_update_status_completed_requires_output(self):
        task = self._make_task(assignee=self.assignee)
        issue_task(self.author, task)
        update_task_status(self.assignee, task, DesignTask.Status.IN_PROGRESS)
        with self.assertRaises(ValidationError):
            update_task_status(self.assignee, task, DesignTask.Status.COMPLETED)
        # После приложения результата переход проходит
        self._output_attachment(task)
        update_task_status(self.assignee, task, DesignTask.Status.COMPLETED)
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.COMPLETED)

    def test_update_status_on_hold_requires_comment(self):
        task = self._task_in_progress()
        with self.assertRaises(ValidationError):
            update_task_status(
                self.assignee, task, DesignTask.Status.ON_HOLD, comment='   ',
            )
        update_task_status(
            self.assignee, task, DesignTask.Status.ON_HOLD,
            comment='Жду шифр фундаментов',
        )
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.ON_HOLD)
        self.assertTrue(
            DesignTaskStatusLog.objects.filter(
                task=task,
                new_status=DesignTask.Status.ON_HOLD,
                comment='Жду шифр фундаментов',
            ).exists()
        )
        # Обратный переход без комментария разрешён
        update_task_status(self.assignee, task, DesignTask.Status.IN_PROGRESS)

    def test_update_status_returned_requires_comment(self):
        task = self._task_in_progress()
        self._output_attachment(task)
        update_task_status(self.assignee, task, DesignTask.Status.COMPLETED)
        update_task_status(self.author, task, DesignTask.Status.REVIEW)
        with self.assertRaises(ValidationError):
            update_task_status(self.author, task, DesignTask.Status.RETURNED)
        update_task_status(
            self.author, task, DesignTask.Status.RETURNED,
            comment='Переделать схему опираний',
        )
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.RETURNED)
        notification = Notification.objects.filter(
            recipient=self.assignee,
        ).order_by('-created_at').first()
        self.assertIsNotNone(notification)
        self.assertIn('возвращено на доработку', notification.message)
        # RETURNED → IN_PROGRESS делает исполнитель
        update_task_status(self.assignee, task, DesignTask.Status.IN_PROGRESS)

    def test_update_status_cancelled(self):
        task = self._make_task(assignee=self.assignee)
        issue_task(self.author, task)
        with self.subTest('отмена без комментария'):
            with self.assertRaises(ValidationError):
                update_task_status(
                    self.author, task, DesignTask.Status.CANCELLED,
                )
        with self.subTest('исполнитель отменять не может'):
            with self.assertRaises(PermissionDenied):
                update_task_status(
                    self.assignee, task, DesignTask.Status.CANCELLED,
                    comment='self cancel',
                )
        update_task_status(
            self.author, task, DesignTask.Status.CANCELLED,
            comment='Задание неактуально',
        )
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.CANCELLED)
        # Терминальные статусы не отменяются и не «переходят»
        with self.subTest('CANCELLED → что-либо'):
            with self.assertRaises(ValidationError):
                update_task_status(
                    self.author, task, DesignTask.Status.IN_PROGRESS,
                )

        accepted = self._make_task(assignee=self.assignee)
        issue_task(self.author, accepted)
        self._allowed_chain(accepted)
        with self.subTest('ACCEPTED → CANCELLED'):
            with self.assertRaises(ValidationError):
                update_task_status(
                    self.author, accepted, DesignTask.Status.CANCELLED,
                    comment='поздно',
                )

    def test_update_status_permissions_per_transition(self):
        task = self._task_in_progress()
        self._output_attachment(task)
        update_task_status(self.assignee, task, DesignTask.Status.COMPLETED)
        # COMPLETED → REVIEW: исполнитель не имеет права, ГИП — имеет
        with self.assertRaises(PermissionDenied):
            update_task_status(
                self.assignee, task, DesignTask.Status.REVIEW,
            )
        update_task_status(self.gip, task, DesignTask.Status.REVIEW)
        task.refresh_from_db()
        self.assertEqual(task.status, DesignTask.Status.REVIEW)


class AddTaskAttachmentTests(DesignTaskTestBase):
    """add_task_attachment: права по kind и статусные ограничения."""

    def test_add_attachment_input_by_author(self):
        task = self._make_task()
        doc_input = SimpleUploadedFile('src.pdf', b'input')
        attachment = add_task_attachment(
            self.author, task, 'input', file=doc_input,
        )
        self.assertEqual(attachment.kind, 'input')
        self.assertEqual(attachment.uploaded_by, self.author)

    def test_add_attachment_input_denied_for_assignee(self):
        task = self._make_task(assignee=self.assignee)
        with self.assertRaises(PermissionDenied):
            add_task_attachment(
                self.assignee, task, 'input',
                file=SimpleUploadedFile('x.pdf', b'x'),
            )

    def test_add_attachment_output_only_in_progress(self):
        task = self._make_task(assignee=self.assignee)
        # В DRAFT результат прикладывать нельзя
        with self.assertRaises(ValidationError):
            self._output_attachment(task)
        issue_task(self.author, task)
        # В ISSUED тоже
        with self.assertRaises(ValidationError):
            self._output_attachment(task)
        update_task_status(self.assignee, task, DesignTask.Status.IN_PROGRESS)
        attachment = self._output_attachment(task)
        self.assertEqual(attachment.kind, 'output')

    def test_add_attachment_output_denied_for_author(self):
        task = self._task_in_progress()
        with self.assertRaises(PermissionDenied):
            add_task_attachment(
                self.author, task, 'output',
                file=SimpleUploadedFile('y.pdf', b'y'),
            )

    def test_add_attachment_comment_permissions(self):
        task = self._make_task(assignee=self.assignee)
        # Любой участник может комментировать
        for user in (self.author, self.assignee, self.gip, self.admin):
            attachment = add_task_attachment(
                user, task, 'comment',
                file=SimpleUploadedFile('c.txt', b'c'),
            )
            self.assertEqual(attachment.kind, 'comment')
        # Чужак — нет
        with self.assertRaises(PermissionDenied):
            add_task_attachment(
                self.outsider, task, 'comment',
                file=SimpleUploadedFile('z.txt', b'z'),
            )

    def test_add_attachment_requires_document_or_file(self):
        task = self._make_task()
        with self.assertRaises(ValidationError):
            add_task_attachment(self.author, task, 'input')
        with self.assertRaises(ValueError):
            add_task_attachment(self.author, task, 'wrong_kind')


class ToggleChecklistItemTests(DesignTaskTestBase):
    """toggle_checklist_item: права и сброс done-полей."""

    def test_toggle_checklist_item_assignee(self):
        task = self._make_task(
            assignee=self.assignee,
            template=self._make_template(),
        )
        item = DesignTaskChecklistItem.objects.filter(task=task).first()
        toggle_checklist_item(self.assignee, item)
        item.refresh_from_db()
        self.assertTrue(item.is_done)
        self.assertEqual(item.done_by, self.assignee)
        self.assertIsNotNone(item.done_at)
        # Снятие отметки сбрасывает done_by/done_at
        toggle_checklist_item(self.assignee, item)
        item.refresh_from_db()
        self.assertFalse(item.is_done)
        self.assertIsNone(item.done_by)
        self.assertIsNone(item.done_at)

    def test_toggle_checklist_item_gip_allowed(self):
        task = self._make_task(
            assignee=self.assignee,
            template=self._make_template(),
        )
        item = DesignTaskChecklistItem.objects.filter(task=task).first()
        toggle_checklist_item(self.gip, item)
        item.refresh_from_db()
        self.assertTrue(item.is_done)
        self.assertEqual(item.done_by, self.gip)

    def test_toggle_checklist_item_not_assignee_denied(self):
        task = self._make_task(
            assignee=self.assignee,
            template=self._make_template(),
        )
        item = DesignTaskChecklistItem.objects.filter(task=task).first()
        with self.assertRaises(PermissionDenied):
            toggle_checklist_item(self.outsider, item)
        item.refresh_from_db()
        self.assertFalse(item.is_done)


class TaskSummaryForGipTests(DesignTaskTestBase):
    """task_summary_for_gip: total, by_status, overdue, on_hold, review."""

    def test_task_summary_for_gip(self):
        today = timezone.localdate()
        overdue = self._make_task(assignee=self.assignee, title='Просрочено')
        issue_task(self.author, overdue)
        DesignTask.objects.filter(pk=overdue.pk).update(
            due_date=today - timedelta(days=3),
        )
        on_hold = self._make_task(assignee=self.assignee, title='Пауза')
        issue_task(self.author, on_hold)
        update_task_status(self.assignee, on_hold, DesignTask.Status.IN_PROGRESS)
        update_task_status(
            self.assignee, on_hold, DesignTask.Status.ON_HOLD,
            comment='Жду данные',
        )
        review = self._task_in_progress()
        self._output_attachment(review)
        update_task_status(self.assignee, review, DesignTask.Status.COMPLETED)
        update_task_status(self.author, review, DesignTask.Status.REVIEW)
        # Просроченное, но уже закрытое задание — не считается overdue
        closed = self._task_in_progress()
        self._output_attachment(closed)
        update_task_status(self.assignee, closed, DesignTask.Status.COMPLETED)
        update_task_status(self.author, closed, DesignTask.Status.REVIEW)
        update_task_status(self.author, closed, DesignTask.Status.ACCEPTED)
        DesignTask.objects.filter(pk=closed.pk).update(
            due_date=today - timedelta(days=10),
        )

        summary = task_summary_for_gip(self.project)
        self.assertEqual(summary['total'], 4)
        self.assertEqual(summary['overdue'], 1)
        self.assertEqual(summary['on_hold'], 1)
        self.assertEqual(summary['awaiting_review'], 1)
        self.assertEqual(summary['by_status']['ON_HOLD'], 1)
        self.assertEqual(summary['by_status']['REVIEW'], 1)
        self.assertEqual(summary['by_status']['ACCEPTED'], 1)
        self.assertEqual(summary['by_status']['ISSUED'], 1)


class ListTasksTests(DesignTaskTestBase):
    """list_tasks: incoming/outgoing/all и права на 'all'."""

    def test_list_tasks_incoming_outgoing_all(self):
        incoming = self._make_task(assignee=self.assignee, title='Входящее')
        issue_task(self.author, incoming)
        outgoing = self._make_task(assignee=self.gip, title='Исполнит ГИП')
        issue_task(self.author, outgoing)

        self.assertEqual(
            list(list_tasks(self.project, self.assignee, 'incoming')),
            [incoming],
        )
        # author создал оба задания
        self.assertEqual(
            set(list_tasks(self.project, self.author, 'outgoing')),
            {incoming, outgoing},
        )
        self.assertEqual(
            set(list_tasks(self.project, self.gip, 'all')),
            {incoming, outgoing},
        )
        # 'all' для не-ГИПа запрещён
        with self.assertRaises(PermissionDenied):
            list_tasks(self.project, self.assignee, 'all')
        # 'incoming' для того, кому ничего не назначено — пустой
        self.assertEqual(
            list(list_tasks(self.project, self.outsider, 'incoming')),
            [],
        )
        with self.assertRaises(ValueError):
            list_tasks(self.project, self.gip, 'wrong_filter')

    def test_list_tasks_select_related(self):
        self._make_task(assignee=self.assignee)
        # superuser: _ensure_project_access — один exists-запрос + список
        with self.assertNumQueries(2):
            for task in list_tasks(self.project, self.admin, 'all'):
                # Обращения по всем select_related не порождают запросов
                self.assertEqual(task.project.code, 'DT-1')
                self.assertEqual(task.source_section.code, 'AR')
                self.assertEqual(task.target_section.code, 'KR')
                self.assertEqual(task.author.username, 'author_dt')
                self.assertIsNotNone(task.assignee.username)
