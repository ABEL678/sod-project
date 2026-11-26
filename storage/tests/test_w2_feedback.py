"""W2-3: троттлинг обратной связи и уведомление автора о смене статуса.

Основание — бэклог аудита-5 (P1): «Троттлинг submit_feedback +
уведомление автору о смене статуса».
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from storage.domain.models import Company, Feedback, UserProfile
from storage.domain.services import (
    FeedbackRateLimited,
    submit_feedback,
    update_feedback_status,
)


User = get_user_model()


class FeedbackW2FixtureMixin:
    def setUp(self):
        self.company = Company.objects.create(name='Компания W2-3')
        self.author = User.objects.create_user(
            'feedback_author',
            password='pass',
            email='author@example.com',
        )
        UserProfile.objects.update_or_create(
            user=self.author,
            defaults={'role': 'viewer', 'company': self.company},
        )[0]
        self.admin = User.objects.create_user(
            'feedback_admin',
            password='pass',
            email='admin@example.com',
        )
        UserProfile.objects.update_or_create(
            user=self.admin,
            defaults={'role': 'admin', 'company': self.company},
        )[0]


class FeedbackRateLimitTests(FeedbackW2FixtureMixin, TestCase):
    @override_settings(
        FEEDBACK_SUBMIT_RATE_LIMIT=2,
        FEEDBACK_SUBMIT_RATE_WINDOW_SECONDS=3600,
    )
    def test_service_rejects_submission_above_limit(self):
        submit_feedback(self.author, self.company, 'Первое обращение')
        submit_feedback(self.author, self.company, 'Второе обращение')

        with self.assertRaises(FeedbackRateLimited) as ctx:
            submit_feedback(self.author, self.company, 'Третье обращение')

        self.assertGreaterEqual(ctx.exception.retry_after, 1)
        self.assertEqual(
            Feedback.objects.filter(author=self.author).count(),
            2,
        )

    @override_settings(
        FEEDBACK_SUBMIT_RATE_LIMIT=1,
        FEEDBACK_SUBMIT_RATE_WINDOW_SECONDS=3600,
    )
    def test_limit_is_isolated_per_user(self):
        submit_feedback(self.author, self.company, 'Обращение автора')

        other = User.objects.create_user(
            'feedback_other',
            password='pass',
            email='other@example.com',
        )
        UserProfile.objects.update_or_create(
            user=other,
            defaults={'role': 'viewer', 'company': self.company},
        )[0]

        created = submit_feedback(other, self.company, 'Обращение другого')
        self.assertEqual(created.author, other)

    @override_settings(
        FEEDBACK_SUBMIT_RATE_LIMIT=1,
        FEEDBACK_SUBMIT_RATE_WINDOW_SECONDS=3600,
    )
    def test_view_returns_429_and_retry_after(self):
        self.client.force_login(self.author)
        url = reverse('storage:feedback_submit')

        first = self.client.post(url, {'message': 'Первый', 'category': 'BUG'})
        second = self.client.post(url, {'message': 'Второй', 'category': 'BUG'})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertGreaterEqual(int(second['Retry-After']), 1)
        self.assertFalse(second.json()['ok'])

    @override_settings(
        FEEDBACK_SUBMIT_RATE_LIMIT=1,
        FEEDBACK_SUBMIT_RATE_WINDOW_SECONDS=3600,
    )
    def test_limit_does_not_block_other_users_view(self):
        self.client.force_login(self.author)
        url = reverse('storage:feedback_submit')
        self.client.post(url, {'message': 'Первый', 'category': 'BUG'})
        self.assertEqual(
            self.client.post(
                url, {'message': 'Второй', 'category': 'BUG'}
            ).status_code,
            429,
        )

        self.client.force_login(self.admin)
        response = self.client.post(
            url, {'message': 'Обращение администратора', 'category': 'BUG'}
        )
        self.assertEqual(response.status_code, 200)


class FeedbackStatusNotificationTests(FeedbackW2FixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.feedback = Feedback.objects.create(
            company=self.company,
            author=self.author,
            category=Feedback.Category.BUG,
            message='Не работает кнопка',
            page_url='/projects/1/',
            contact_email='',
        )

    @mock.patch('storage.tasks.send_notification_email.delay')
    def test_status_change_emails_author(self, delay):
        update_feedback_status(
            self.admin,
            self.feedback,
            Feedback.Status.IN_PROGRESS,
            admin_comment='Взяли в работу',
        )

        delay.assert_called_once()
        recipient, subject, body = delay.call_args.args
        self.assertEqual(recipient, 'author@example.com')
        self.assertIn(f'#{self.feedback.pk}', subject)
        self.assertIn(Feedback.Status.NEW.label, body)
        self.assertIn(Feedback.Status.IN_PROGRESS.label, body)
        self.assertIn('Взяли в работу', body)
        self.assertIn('/projects/1/', body)

    @mock.patch('storage.tasks.send_notification_email.delay')
    def test_same_status_does_not_email(self, delay):
        update_feedback_status(
            self.admin,
            self.feedback,
            Feedback.Status.NEW,
            admin_comment='Комментарий без смены статуса',
        )

        delay.assert_not_called()

    @mock.patch('storage.tasks.send_notification_email.delay')
    def test_missing_email_does_not_fail_change(self, delay):
        self.author.email = ''
        self.author.save(update_fields=['email'])
        self.feedback.contact_email = ''
        self.feedback.save(update_fields=['contact_email'])

        updated = update_feedback_status(
            self.admin,
            self.feedback,
            Feedback.Status.RESOLVED,
        )

        self.assertEqual(updated.status, Feedback.Status.RESOLVED)
        delay.assert_not_called()

    @mock.patch('storage.tasks.send_notification_email.delay')
    def test_email_failure_does_not_rollback_status(self, delay):
        delay.side_effect = RuntimeError('SMTP недоступен')

        updated = update_feedback_status(
            self.admin,
            self.feedback,
            Feedback.Status.REJECTED,
        )

        updated.refresh_from_db()
        self.assertEqual(updated.status, Feedback.Status.REJECTED)

    @mock.patch('storage.tasks.send_notification_email.delay')
    def test_author_action_does_not_email_self(self, delay):
        self.author.is_superuser = True
        self.author.save(update_fields=['is_superuser'])

        update_feedback_status(
            self.author,
            self.feedback,
            Feedback.Status.RESOLVED,
        )

        delay.assert_not_called()
