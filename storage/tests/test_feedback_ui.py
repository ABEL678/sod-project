from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from storage.domain.models import Feedback
from storage.domain.services import (
    submit_feedback,
    update_feedback_status,
)
from storage.tests.test_feedback import FeedbackFixtureMixin, TINY_PNG


class FeedbackUITests(FeedbackFixtureMixin, TestCase):
    def _post_feedback(self, **overrides):
        payload = {
            'message': 'Проверка формы',
            'category': 'BUG',
            'page_url': '/projects/1/',
        }
        payload.update(overrides)
        self.client.force_login(self.user_a)
        return self.client.post(
            reverse('storage:feedback_submit'),
            payload,
        )

    def test_large_screenshot_rejected(self):
        big_file = SimpleUploadedFile(
            'big.png',
            b'x' * (10 * 1024 * 1024 + 1),
            content_type='image/png',
        )

        response = self._post_feedback(screenshot=big_file)

        self.assertEqual(response.status_code, 400)
        self.assertIn('screenshot', response.json()['errors'])

    def test_invalid_screenshot_mime_rejected(self):
        bad_file = SimpleUploadedFile(
            'bad.gif',
            b'GIF89a',
            content_type='image/gif',
        )

        response = self._post_feedback(screenshot=bad_file)

        self.assertEqual(response.status_code, 400)
        self.assertIn('screenshot', response.json()['errors'])

    def test_long_filename_produces_short_storage_path(self):
        long_file = SimpleUploadedFile(
            'a' * 120 + '.png',
            TINY_PNG,
            content_type='image/png',
        )

        response = self._post_feedback(screenshot=long_file)

        self.assertEqual(response.status_code, 200)
        feedback = Feedback.objects.get(pk=response.json()['id'])
        self.assertLessEqual(len(feedback.screenshot.name), 100)
        self.assertTrue(feedback.screenshot.name.endswith('.png'))
        self.assertTrue(
            feedback.screenshot.storage.exists(feedback.screenshot.name)
        )

    def test_update_feedback_status_permissions(self):
        feedback = submit_feedback(
            self.user_a,
            self.company_a,
            'Проверка статуса',
        )

        updated = update_feedback_status(
            self.admin_a,
            feedback,
            Feedback.Status.RESOLVED,
            'Проверено',
        )
        self.assertEqual(updated.status, Feedback.Status.RESOLVED)
        self.assertEqual(updated.admin_comment, 'Проверено')

        with self.assertRaises(PermissionDenied):
            update_feedback_status(
                self.user_a,
                feedback,
                Feedback.Status.IN_PROGRESS,
            )

        with self.assertRaises(PermissionDenied):
            update_feedback_status(
                self.admin_b,
                feedback,
                Feedback.Status.REJECTED,
            )

        with self.assertRaises(ValidationError):
            update_feedback_status(
                self.admin_a,
                feedback,
                'UNKNOWN',
            )

        with self.assertRaises(ValidationError):
            update_feedback_status(
                self.admin_a,
                feedback,
                Feedback.Status.RESOLVED,
                'x' * 2001,
            )

    def test_status_update_view_changes_status(self):
        feedback = submit_feedback(
            self.user_a,
            self.company_a,
            'Проверка AJAX-статуса',
        )
        url = reverse(
            'storage:feedback_status_update',
            args=[feedback.pk],
        )

        self.client.force_login(self.admin_a)
        response = self.client.post(
            url,
            {'status': 'RESOLVED', 'admin_comment': 'Готово'},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['status'], 'RESOLVED')
        feedback.refresh_from_db()
        self.assertEqual(feedback.status, 'RESOLVED')
        self.assertEqual(feedback.admin_comment, 'Готово')

        self.client.force_login(self.user_a)
        response = self.client.post(url, {'status': 'REJECTED'})
        self.assertEqual(response.status_code, 403)

    def test_status_update_without_status_keeps_current_status(self):
        feedback = submit_feedback(
            self.user_a,
            self.company_a,
            'Комментарий без смены статуса',
        )
        original_status = feedback.status
        url = reverse(
            'storage:feedback_status_update',
            args=[feedback.pk],
        )

        self.client.force_login(self.admin_a)
        response = self.client.post(
            url,
            {'admin_comment': 'Только комментарий'},
        )

        self.assertEqual(response.status_code, 200)
        feedback.refresh_from_db()
        self.assertEqual(feedback.status, original_status)
        self.assertEqual(feedback.admin_comment, 'Только комментарий')

    def test_pagination_preserves_filters(self):
        for index in range(51):
            Feedback.objects.create(
                company=self.company_a,
                author=self.user_a,
                category=Feedback.Category.BUG,
                status=Feedback.Status.NEW,
                message=f'Обращение {index}',
            )

        self.client.force_login(self.admin_a)
        response = self.client.get(
            reverse('storage:feedback_list'),
            {'status': 'NEW', 'category': 'BUG', 'page': 2},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].number, 2)
        html = response.content.decode()
        self.assertIn('status=NEW', html)
        self.assertIn('category=BUG', html)

    def test_superuser_can_select_company(self):
        feedback_a = submit_feedback(
            self.user_a,
            self.company_a,
            'Фидбек компании A',
        )
        feedback_b = submit_feedback(
            self.user_b,
            self.company_b,
            'Фидбек компании B',
        )

        self.client.force_login(self.superuser)
        response = self.client.get(
            reverse('storage:feedback_list'),
            {'company': self.company_b.pk},
        )

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn(feedback_b.message, html)
        self.assertNotIn(feedback_a.message, html)
        self.assertIn('name="company"', html)


    def test_topbar_and_modal_markup_present(self):
        self.client.force_login(self.user_a)
        response = self.client.get(reverse('storage:project_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'bi-bug')
        self.assertContains(response, 'id="feedbackModal"')
        self.assertContains(response, 'data-bs-target="#feedbackModal"')

    def test_admin_page_has_filters_and_detail_modal(self):
        submit_feedback(
            self.user_a,
            self.company_a,
            'Проверка разметки админки',
        )

        self.client.force_login(self.admin_a)
        response = self.client.get(reverse('storage:feedback_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="status"')
        self.assertContains(response, 'name="category"')
        self.assertContains(response, 'id="feedbackDetailModal"')
        self.assertContains(response, 'data-status-url')

