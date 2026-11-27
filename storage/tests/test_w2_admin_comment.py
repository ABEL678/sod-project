"""W2-5: очистка комментария администратора в обратной связи.

Основание — бэклог аудита-5 (P2): «Очистка admin_comment в feedback»
(B-5: пустая строка игнорировалась, поэтому комментарий нельзя было стереть).
"""

from pathlib import Path
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from storage.domain.models import Feedback
from storage.domain.services import update_feedback_status
from storage.tests.test_w2_feedback import FeedbackW2FixtureMixin


class FeedbackAdminCommentServiceTests(FeedbackW2FixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.feedback = Feedback.objects.create(
            company=self.company,
            author=self.author,
            category=Feedback.Category.BUG,
            message='Не работает кнопка',
            admin_comment='Старый комментарий',
        )

    def test_empty_string_clears_comment(self):
        updated = update_feedback_status(
            self.admin,
            self.feedback,
            Feedback.Status.NEW,
            admin_comment='',
        )

        self.assertEqual(updated.admin_comment, '')
        self.feedback.refresh_from_db()
        self.assertEqual(self.feedback.admin_comment, '')

    def test_whitespace_only_clears_comment(self):
        updated = update_feedback_status(
            self.admin,
            self.feedback,
            Feedback.Status.NEW,
            admin_comment='   ',
        )

        self.assertEqual(updated.admin_comment, '')

    def test_none_preserves_comment(self):
        updated = update_feedback_status(
            self.admin,
            self.feedback,
            Feedback.Status.NEW,
        )

        self.assertEqual(updated.admin_comment, 'Старый комментарий')

    def test_non_empty_comment_replaces_existing(self):
        updated = update_feedback_status(
            self.admin,
            self.feedback,
            Feedback.Status.NEW,
            admin_comment='  Новый комментарий  ',
        )

        self.assertEqual(updated.admin_comment, 'Новый комментарий')

    @mock.patch('storage.tasks.send_notification_email.delay')
    def test_status_change_with_cleared_comment_omits_comment_from_email(
        self,
        delay,
    ):
        update_feedback_status(
            self.admin,
            self.feedback,
            Feedback.Status.RESOLVED,
            admin_comment='',
        )

        delay.assert_called_once()
        _, _, body = delay.call_args.args
        self.assertNotIn('Старый комментарий', body)
        self.assertNotIn('Комментарий:', body)


class FeedbackAdminCommentViewTests(FeedbackW2FixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.feedback = Feedback.objects.create(
            company=self.company,
            author=self.author,
            category=Feedback.Category.BUG,
            message='Не работает кнопка',
            admin_comment='Старый комментарий',
        )
        self.url = reverse(
            'storage:feedback_status_update',
            args=[self.feedback.pk],
        )
        self.client.force_login(self.admin)

    def test_view_empty_comment_clears_comment(self):
        response = self.client.post(self.url, {'admin_comment': ''})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.feedback.refresh_from_db()
        self.assertEqual(self.feedback.admin_comment, '')

    def test_view_whitespace_comment_clears_comment(self):
        response = self.client.post(self.url, {'admin_comment': '   '})

        self.assertEqual(response.status_code, 200)
        self.feedback.refresh_from_db()
        self.assertEqual(self.feedback.admin_comment, '')

    @mock.patch('storage.tasks.send_notification_email.delay')
    def test_view_missing_comment_preserves_comment(self, delay):
        response = self.client.post(self.url, {'status': 'IN_PROGRESS'})

        self.assertEqual(response.status_code, 200)
        self.feedback.refresh_from_db()
        self.assertEqual(self.feedback.admin_comment, 'Старый комментарий')
        delay.assert_called_once()

    def test_detail_help_text_mentions_clearing(self):
        response = self.client.get(reverse('storage:feedback_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Пустое поле очищает комментарий.')


class FeedbackAdminCommentUIContractTests(TestCase):
    def test_js_sends_empty_comment_from_detail_modal(self):
        script = (
            Path(__file__).resolve().parents[2] / 'static' / 'js' / 'feedback.js'
        ).read_text(encoding='utf-8')

        self.assertIn("body.append('admin_comment', comment || '')", script)
        self.assertIn('if (comment || fromDetail)', script)
