"""Уведомления и напоминания по заданиям — Этап 5 (спека DESIGN_TASKS_SPEC).

Покрывает:
- триггеры уведомлений при issue_task и каждом переходе update_task_status
  (получатель, текст, ссылка на карточку);
- безопасность: уведомления не утекают за пределы проекта
  (пользователь другой компании уведомлений не получает);
- Celery-задачи напоминаний notify_task_due_soon / due_today / overdue
  (фильтры по статусам и срокам, идемпотентность, один запрос выборки);
- расписание celery-beat (CELERY_BEAT_SCHEDULE) и регистрацию задач.
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from celery import current_app
from django.conf import settings as django_settings

from issues.models import Notification
from storage.domain.models import DesignTask
from storage.domain.services import (
    issue_task,
    notify_tasks_due_soon,
    notify_tasks_due_today,
    notify_tasks_overdue,
    update_task_status,
)
from storage.tasks import (
    notify_task_due_soon,
    notify_task_due_today,
    notify_task_overdue,
)

from .test_design_tasks import DesignTaskTestBase, add_task_attachment

TASK_URL = '/projects/{project_id}/tasks/{task_id}/'


class TaskStatusNotificationTests(DesignTaskTestBase):
    """Триггеры уведомлений при смене статусов (матрица событий Этапа 5)."""

    def _last(self, user):
        return (
            Notification.objects.filter(recipient=user)
            .order_by('-created_at')
            .first()
        )

    def _task_issued(self):
        task = self._make_task(assignee=self.assignee)
        issue_task(self.author, task)
        return task

    def test_issue_task_notifies_assignee(self):
        task = self._make_task(assignee=self.assignee)
        issue_task(self.author, task)
        notification = self._last(self.assignee)
        self.assertIsNotNone(notification)
        self.assertEqual(notification.verb, 'assigned')
        self.assertEqual(notification.actor, self.author)
        self.assertIn('Вам выдано задание', notification.message)
        self.assertIn(f'#{task.pk}', notification.message)
        self.assertEqual(
            notification.url,
            TASK_URL.format(project_id=self.project.pk, task_id=task.pk),
        )

    def test_in_progress_notifies_author_and_gip(self):
        task = self._task_issued()
        Notification.objects.all().delete()
        update_task_status(self.assignee, task, DesignTask.Status.IN_PROGRESS)
        messages = {
            n.recipient_id: n.message
            for n in Notification.objects.all()
        }
        self.assertIn(self.author.pk, messages)
        self.assertIn(self.gip.pk, messages)
        self.assertIn('принято в работу', messages[self.author.pk])
        self.assertIn(f'#{task.pk}', messages[self.author.pk])
        # Актор (исполнитель) уведомления о своём действии не получает.
        self.assertNotIn(self.assignee.pk, messages)

    def test_on_hold_notifies_with_reason(self):
        task = self._task_in_progress()
        Notification.objects.all().delete()
        update_task_status(
            self.assignee, task, DesignTask.Status.ON_HOLD,
            comment='Нет исходных данных по КР',
        )
        notification = self._last(self.gip)
        self.assertIsNotNone(notification)
        self.assertIn('приостановлено', notification.message)
        self.assertIn('Причина: Нет исходных данных по КР', notification.message)

    def test_resume_notifies(self):
        task = self._task_in_progress()
        update_task_status(
            self.assignee, task, DesignTask.Status.ON_HOLD, comment='Ждём данные',
        )
        Notification.objects.all().delete()
        update_task_status(self.assignee, task, DesignTask.Status.IN_PROGRESS)
        notification = self._last(self.author)
        self.assertIsNotNone(notification)
        self.assertIn('возобновлено', notification.message)

    def test_completed_notifies_author_and_gip(self):
        task = self._task_in_progress()
        add_task_attachment(
            self.assignee, task, 'output',
            file=self._uploaded('result.pdf', b'result'),
        )
        Notification.objects.all().delete()
        update_task_status(self.assignee, task, DesignTask.Status.COMPLETED)
        messages = {
            n.recipient_id: n.message
            for n in Notification.objects.all()
        }
        self.assertIn(self.author.pk, messages)
        self.assertIn(self.gip.pk, messages)
        self.assertIn('ожидает проверки', messages[self.author.pk])

    def test_sent_to_review_notifies_assignee(self):
        task = self._task_in_progress()
        add_task_attachment(
            self.assignee, task, 'output',
            file=self._uploaded('result.pdf', b'result'),
        )
        # IN_PROGRESS → COMPLETED делает исполнитель, COMPLETED → REVIEW —
        # автор (матрица переходов).
        update_task_status(self.assignee, task, DesignTask.Status.COMPLETED)
        Notification.objects.all().delete()
        update_task_status(self.author, task, DesignTask.Status.REVIEW)
        notification = self._last(self.assignee)
        self.assertIsNotNone(notification)
        self.assertIn('отправлено на проверку', notification.message)

    def test_returned_message_includes_reason(self):
        task = self._task_in_progress()
        add_task_attachment(
            self.assignee, task, 'output',
            file=self._uploaded('result.pdf', b'result'),
        )
        update_task_status(self.assignee, task, DesignTask.Status.COMPLETED)
        update_task_status(self.author, task, DesignTask.Status.REVIEW)
        Notification.objects.all().delete()
        update_task_status(
            self.author, task, DesignTask.Status.RETURNED,
            comment='Переделать схему опираний',
        )
        notification = self._last(self.assignee)
        self.assertIsNotNone(notification)
        self.assertIn('возвращено на доработку', notification.message)
        self.assertIn('Причина: Переделать схему опираний', notification.message)

    def test_accepted_notifies_assignee(self):
        task = self._task_in_progress()
        add_task_attachment(
            self.assignee, task, 'output',
            file=self._uploaded('result.pdf', b'result'),
        )
        update_task_status(self.assignee, task, DesignTask.Status.COMPLETED)
        update_task_status(self.author, task, DesignTask.Status.REVIEW)
        Notification.objects.all().delete()
        update_task_status(self.author, task, DesignTask.Status.ACCEPTED)
        notification = self._last(self.assignee)
        self.assertIsNotNone(notification)
        self.assertIn('принято', notification.message)

    def test_cancelled_notifies_assignee_and_author(self):
        task = self._task_issued()
        Notification.objects.all().delete()
        update_task_status(
            self.gip, task, DesignTask.Status.CANCELLED, comment='Проект закрыт',
        )
        messages = {
            n.recipient_id: n.message
            for n in Notification.objects.all()
        }
        self.assertIn(self.assignee.pk, messages)
        self.assertIn(self.author.pk, messages)
        self.assertIn('отменено', messages[self.assignee.pk])
        self.assertIn('Причина: Проект закрыт', messages[self.assignee.pk])
        # Актор (ГИП) не уведомляется.
        self.assertNotIn(self.gip.pk, messages)

    def _uploaded(self, name, content):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(name, content)


class TaskNotificationSecurityTests(DesignTaskTestBase):
    """Уведомления не утекают за пределы проекта (спека Этап 5)."""

    def test_recipient_from_other_company_gets_nothing(self):
        other_company_user = self._user_other_company()
        task = self._make_task(assignee=other_company_user)
        issue_task(self.author, task)
        self.assertFalse(
            Notification.objects.filter(recipient=other_company_user).exists(),
        )
        # Смена статуса исполнителем — автор/ГИП получают уведомление,
        # «чужой» исполнитель — нет.
        update_task_status(other_company_user, task, DesignTask.Status.IN_PROGRESS)
        self.assertFalse(
            Notification.objects.filter(recipient=other_company_user).exists(),
        )
        self.assertTrue(
            Notification.objects.filter(recipient=self.author).exists(),
        )

    def test_company_member_counts_as_participant(self):
        """Участник компании проекта получает уведомления (граница — компания)."""
        colleague = self._user_colleague()
        task = self._make_task(assignee=colleague)
        issue_task(self.author, task)
        self.assertTrue(
            Notification.objects.filter(recipient=colleague).exists(),
        )

    def _user_other_company(self):
        from storage.domain.models import Company, UserProfile
        user = self._make_user('other_co')
        other = Company.objects.create(name='Другая компания')
        UserProfile.objects.update_or_create(
            user=user, defaults={'role': 'developer', 'company': other},
        )
        return user

    def _user_colleague(self):
        from storage.domain.models import UserProfile
        user = self._make_user('colleague_co')
        UserProfile.objects.update_or_create(
            user=user,
            defaults={'role': 'developer', 'company': self.company},
        )
        return user

    def _make_user(self, name):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.create_user(name, password='pass')


class ReminderTasksTests(DesignTaskTestBase):
    """Celery-задачи напоминаний: фильтры, идемпотентность, запросы."""

    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()

    def _issued_task(self, due=None):
        task = self._make_task(assignee=self.assignee)
        if due is not None:
            task.due_date = due
            task.save(update_fields=['due_date'])
        issue_task(self.author, task)
        return task

    def test_due_soon_notifies_assignee(self):
        self._issued_task(due=self.today + timedelta(days=2))
        created = notify_tasks_due_soon()
        self.assertEqual(created, 1)
        notification = Notification.objects.first()
        self.assertEqual(notification.recipient, self.assignee)
        self.assertIn('срок через 2 дня', notification.message)
        self.assertIsNone(notification.actor)

    def test_due_soon_window_is_exact(self):
        self._issued_task(due=self.today + timedelta(days=1))
        self._issued_task(due=self.today + timedelta(days=3))
        self.assertEqual(notify_tasks_due_soon(), 0)

    def test_due_soon_skips_draft_and_closed(self):
        # Черновик: срок подошёл, но задание не выдано — напоминания нет.
        draft = self._make_task(assignee=self.assignee)
        draft.due_date = self.today + timedelta(days=2)
        draft.save(update_fields=['due_date'])
        # Выполненное задание (открытый цикл завершён) — напоминания нет.
        closed = self._task_in_progress()
        closed.due_date = self.today + timedelta(days=2)
        closed.save(update_fields=['due_date'])
        add_task_attachment(
            self.assignee, closed, 'output',
            file=self._uploaded('result.pdf', b'result'),
        )
        update_task_status(self.assignee, closed, DesignTask.Status.COMPLETED)
        # Выданное задание — единственное напоминание.
        self._issued_task(due=self.today + timedelta(days=2))
        self.assertEqual(notify_tasks_due_soon(), 1)

    def test_due_today_notifies_assignee(self):
        self._issued_task(due=self.today)
        self.assertEqual(notify_tasks_due_today(), 1)
        self.assertIn(
            'срок истекает сегодня',
            Notification.objects.first().message,
        )

    def test_due_today_skips_other_days(self):
        self._issued_task(due=self.today + timedelta(days=1))
        self._issued_task(due=self.today - timedelta(days=1))
        self.assertEqual(notify_tasks_due_today(), 0)

    def test_overdue_notifies_assignee_author_gip(self):
        task = self._issued_task(due=self.today - timedelta(days=1))
        created = notify_tasks_overdue()
        self.assertEqual(created, 3)
        recipients = set(
            Notification.objects.values_list('recipient_id', flat=True)
        )
        self.assertEqual(
            recipients,
            {self.assignee.pk, self.author.pk, self.gip.pk},
        )
        self.assertIn('просрочено', Notification.objects.first().message)
        self.assertIn(f'#{task.pk}', Notification.objects.first().message)

    def test_overdue_skips_terminal_statuses(self):
        # COMPLETED
        done = self._task_in_progress()
        done.due_date = self.today - timedelta(days=1)
        done.save(update_fields=['due_date'])
        add_task_attachment(
            self.assignee, done, 'output',
            file=self._uploaded('result.pdf', b'result'),
        )
        update_task_status(self.assignee, done, DesignTask.Status.COMPLETED)
        # ACCEPTED
        accepted = self._task_in_progress()
        accepted.due_date = self.today - timedelta(days=1)
        accepted.save(update_fields=['due_date'])
        add_task_attachment(
            self.assignee, accepted, 'output',
            file=self._uploaded('result.pdf', b'result'),
        )
        update_task_status(self.assignee, accepted, DesignTask.Status.COMPLETED)
        update_task_status(self.author, accepted, DesignTask.Status.REVIEW)
        update_task_status(self.author, accepted, DesignTask.Status.ACCEPTED)
        # CANCELLED
        cancelled = self._issued_task(due=self.today - timedelta(days=1))
        update_task_status(
            self.gip, cancelled, DesignTask.Status.CANCELLED, comment='Отмена',
        )
        self.assertEqual(notify_tasks_overdue(), 0)

    def test_overdue_includes_returned(self):
        task = self._issued_task(due=self.today - timedelta(days=1))
        update_task_status(self.assignee, task, DesignTask.Status.IN_PROGRESS)
        add_task_attachment(
            self.assignee, task, 'output',
            file=self._uploaded('result.pdf', b'result'),
        )
        update_task_status(self.assignee, task, DesignTask.Status.COMPLETED)
        update_task_status(self.author, task, DesignTask.Status.REVIEW)
        update_task_status(
            self.author, task, DesignTask.Status.RETURNED, comment='Доработать',
        )
        self.assertEqual(notify_tasks_overdue(), 3)

    def test_idempotent_repeats_do_not_duplicate(self):
        self._issued_task(due=self.today - timedelta(days=1))
        self.assertEqual(notify_tasks_overdue(), 3)
        self.assertEqual(notify_tasks_overdue(), 0)
        # «Вам выдано задание» + 3 напоминания о просрочке; ретрай не
        # добавляет четвёртого напоминания.
        self.assertEqual(
            Notification.objects.filter(
                message__contains='просрочено',
            ).count(),
            3,
            'Ретрай/повторный запуск не создаёт дубликаты напоминаний',
        )

    def test_deadline_tasks_use_single_query(self):
        """Производительность: один запрос на выборку заданий (select_related)."""
        self._issued_task(due=self.today - timedelta(days=1))
        with self.assertNumQueries(3):
            # 1 — выборка заданий, 2 — идемпотентность, 3 — bulk_create.
            notify_tasks_overdue()

    def _uploaded(self, name, content):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(name, content)


class CeleryTasksAndBeatTests(TestCase):
    """Celery-задачи зарегистрированы, расписание beat настроено."""

    TASK_NAMES = (
        'storage.tasks.notify_task_due_soon',
        'storage.tasks.notify_task_due_today',
        'storage.tasks.notify_task_overdue',
    )

    def test_tasks_registered_on_app(self):
        app = current_app
        app.finalize()
        for name in self.TASK_NAMES:
            self.assertIn(name, app.tasks)

    def test_beat_schedule_has_three_entries(self):
        schedule = django_settings.CELERY_BEAT_SCHEDULE
        scheduled = {entry['task'] for entry in schedule.values()}
        for name in self.TASK_NAMES:
            self.assertIn(name, scheduled)

    def test_due_tasks_run_daily_overdue_hourly(self):
        schedule = django_settings.CELERY_BEAT_SCHEDULE
        due_soon = schedule['notify-task-due-soon']['schedule']
        due_today = schedule['notify-task-due-today']['schedule']
        overdue = schedule['notify-task-overdue']['schedule']
        # due_* — раз в сутки: ровно один час дня (crontab(hour=8, ...)).
        self.assertEqual(due_soon.hour, {8})
        self.assertEqual(due_soon.minute, {0})
        self.assertEqual(due_today.hour, {8})
        self.assertEqual(due_today.minute, {15})
        # overdue — раз в час: все 24 часа, минута фиксирована
        # (crontab(minute=0) → hour разворачивается в 0..23).
        self.assertEqual(overdue.hour, set(range(24)))
        self.assertEqual(overdue.minute, {0})
        # Часовой пояс Celery — часовой пояс проекта (Москва).
        self.assertEqual(django_settings.CELERY_TIMEZONE, 'Europe/Moscow')

    def test_eager_delay_runs_task_inline(self):
        """Интеграция: .delay() в eager-режиме выполняет задачу синхронно."""
        # Без заданий задача отрабатывает и возвращает 0.
        result = notify_task_due_soon.delay()
        self.assertEqual(result.get(), 0)
        self.assertEqual(notify_task_due_today.delay().get(), 0)
        self.assertEqual(notify_task_overdue.delay().get(), 0)

    def test_apply_async_mock(self):
        """Beat вызывает apply_async — мок подтверждает точку вызова."""
        with mock.patch.object(notify_task_overdue, 'apply_async') as mocked:
            notify_task_overdue.apply_async()
        mocked.assert_called_once()
        with mock.patch.object(notify_task_due_soon, 'apply_async') as mocked:
            notify_task_due_soon.apply_async()
        mocked.assert_called_once()
        with mock.patch.object(notify_task_due_today, 'apply_async') as mocked:
            notify_task_due_today.apply_async()
        mocked.assert_called_once()
