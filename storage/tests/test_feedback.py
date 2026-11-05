import base64
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from storage.adapters.local_storage import LocalFileStorage
from storage.domain.models import Company, Feedback, UserProfile
from storage.domain.services import list_feedback, submit_feedback


User = get_user_model()

TINY_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk'
    '+A8AAQUBAScY42YAAAAASUVORK5CYII='
)


class FeedbackFixtureMixin:
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)

        self.company_a = Company.objects.create(name='Компания A')
        self.company_b = Company.objects.create(name='Компания B')

        self.user_a = User.objects.create_user(
            'user_a',
            password='pass',
            email='user_a@example.com',
        )
        UserProfile.objects.update_or_create(user=self.user_a, defaults={'role': 'viewer', 'company': self.company_a})[0]

        self.admin_a = User.objects.create_user(
            'admin_a',
            password='pass',
            email='admin_a@example.com',
        )
        UserProfile.objects.update_or_create(user=self.admin_a, defaults={'role': 'admin', 'company': self.company_a})[0]

        self.user_b = User.objects.create_user(
            'user_b',
            password='pass',
            email='user_b@example.com',
        )
        UserProfile.objects.update_or_create(user=self.user_b, defaults={'role': 'viewer', 'company': self.company_b})[0]

        self.admin_b = User.objects.create_user(
            'admin_b',
            password='pass',
            email='admin_b@example.com',
        )
        UserProfile.objects.update_or_create(user=self.admin_b, defaults={'role': 'admin', 'company': self.company_b})[0]

        self.superuser = User.objects.create_superuser(
            'root',
            password='pass',
            email='root@example.com',
        )


class FeedbackServiceTests(FeedbackFixtureMixin, TestCase):
    def test_submit_feedback_creates_record_and_fills_email(self):
        feedback = submit_feedback(
            self.user_a,
            self.company_a,
            '  Не работает кнопка  ',
            category=Feedback.Category.BUG,
            page_url='/projects/1/',
        )

        self.assertEqual(feedback.company, self.company_a)
        self.assertEqual(feedback.author, self.user_a)
        self.assertEqual(feedback.message, 'Не работает кнопка')
        self.assertEqual(feedback.category, Feedback.Category.BUG)
        self.assertEqual(feedback.contact_email, 'user_a@example.com')
        self.assertEqual(feedback.status, Feedback.Status.NEW)
        self.assertEqual(feedback.page_url, '/projects/1/')

    def test_submit_feedback_saves_screenshot_via_storage(self):
        upload = SimpleUploadedFile(
            'screen.png',
            TINY_PNG,
            content_type='image/png',
        )

        feedback = submit_feedback(
            self.user_a,
            self.company_a,
            'Скриншот',
            screenshot=upload,
            file_storage=LocalFileStorage(),
        )

        self.assertTrue(
            feedback.screenshot.name.startswith('feedback/screenshots/')
        )
        self.assertTrue(feedback.screenshot.storage.exists(feedback.screenshot.name))
        self.assertGreater(feedback.screenshot.size, 0)

    def test_submit_feedback_validates_input(self):
        with self.assertRaises(ValidationError):
            submit_feedback(self.user_a, self.company_a, '   ')

        with self.assertRaises(ValidationError):
            submit_feedback(self.user_a, self.company_a, 'x' * 5001)

        with self.assertRaises(ValidationError):
            submit_feedback(
                self.user_a,
                self.company_a,
                'Текст',
                category='UNKNOWN',
            )

        with self.assertRaises(ValidationError):
            submit_feedback(
                self.user_a,
                self.company_a,
                'Текст',
                contact_email='not-an-email',
            )

    def test_submit_feedback_denies_foreign_company(self):
        with self.assertRaises(PermissionDenied):
            submit_feedback(self.user_a, self.company_b, 'Текст')

    def test_list_feedback_filters_company_and_requires_admin(self):
        feedback_a = submit_feedback(
            self.user_a,
            self.company_a,
            'Фидбек A',
        )
        feedback_b = submit_feedback(
            self.user_b,
            self.company_b,
            'Фидбек B',
        )

        items_a = list(list_feedback(self.admin_a, self.company_a))
        self.assertIn(feedback_a, items_a)
        self.assertNotIn(feedback_b, items_a)

        items_b = list(list_feedback(self.admin_b, self.company_b))
        self.assertIn(feedback_b, items_b)
        self.assertNotIn(feedback_a, items_b)

        with self.assertRaises(PermissionDenied):
            list_feedback(self.user_a, self.company_a)

        self.assertIn(
            feedback_a,
            list(list_feedback(self.superuser, self.company_a)),
        )

    def test_list_feedback_applies_status_and_category_filters(self):
        bug = submit_feedback(
            self.user_a,
            self.company_a,
            'Баг',
            category=Feedback.Category.BUG,
        )
        idea = submit_feedback(
            self.user_a,
            self.company_a,
            'Идея',
            category=Feedback.Category.IDEA,
        )
        bug.status = Feedback.Status.RESOLVED
        bug.save(update_fields=['status'])

        by_category = list(
            list_feedback(
                self.admin_a,
                self.company_a,
                category=Feedback.Category.IDEA,
            )
        )
        self.assertEqual(by_category, [idea])

        by_status = list(
            list_feedback(
                self.admin_a,
                self.company_a,
                status=Feedback.Status.RESOLVED,
            )
        )
        self.assertEqual(by_status, [bug])

        with self.assertRaises(ValidationError):
            list_feedback(
                self.admin_a,
                self.company_a,
                status='UNKNOWN',
            )


class FeedbackViewTests(FeedbackFixtureMixin, TestCase):
    def test_submit_view_creates_feedback(self):
        self.client.force_login(self.user_a)
        response = self.client.post(
            reverse('storage:feedback_submit'),
            {'message': 'Баг из модалки', 'category': 'BUG'},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        feedback = Feedback.objects.get(pk=payload['id'])
        self.assertEqual(feedback.author, self.user_a)
        self.assertEqual(feedback.company, self.company_a)

    def test_submit_view_returns_400_for_empty_message(self):
        self.client.force_login(self.user_a)
        response = self.client.post(
            reverse('storage:feedback_submit'),
            {'message': '   ', 'category': 'BUG'},
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload['ok'])
        self.assertIn('message', payload['errors'])

    def test_submit_view_accepts_screenshot(self):
        self.client.force_login(self.user_a)
        upload = SimpleUploadedFile(
            'screen.png',
            TINY_PNG,
            content_type='image/png',
        )
        response = self.client.post(
            reverse('storage:feedback_submit'),
            {
                'message': 'Со скриншотом',
                'category': 'UX',
                'screenshot': upload,
            },
        )

        self.assertEqual(response.status_code, 200)
        feedback = Feedback.objects.get(pk=response.json()['id'])
        self.assertTrue(feedback.screenshot.name)

    def test_feedback_list_available_only_to_admin(self):
        submit_feedback(self.user_a, self.company_a, 'Фидбек A')

        self.client.force_login(self.admin_a)
        response = self.client.get(reverse('storage:feedback_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Обратная связь')
        self.assertContains(response, 'Фидбек A')

        self.client.force_login(self.user_a)
        response = self.client.get(reverse('storage:feedback_list'))
        self.assertEqual(response.status_code, 403)

    def test_admin_does_not_see_other_company_feedback(self):
        submit_feedback(self.user_b, self.company_b, 'Секрет компании B')

        self.client.force_login(self.admin_a)
        response = self.client.get(reverse('storage:feedback_list'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Секрет компании B')

    def test_submit_view_requires_login(self):
        response = self.client.post(
            reverse('storage:feedback_submit'),
            {'message': 'Без логина'},
        )
        self.assertEqual(response.status_code, 302)


    def test_superuser_without_profile_gets_company_and_feedback(self):
        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse('storage:feedback_submit'),
            {'message': 'Фидбек суперюзера', 'category': 'BUG'},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        first_company = Company.objects.order_by('pk').first()
        profile = UserProfile.objects.get(user=self.superuser)
        self.assertEqual(profile.company, first_company)
        feedback = Feedback.objects.get(pk=payload['id'])
        self.assertEqual(feedback.author, self.superuser)
        self.assertEqual(feedback.company, first_company)

    def test_user_without_company_gets_400(self):
        user = User.objects.create_user('no_company', password='pass')
        self.client.force_login(user)
        response = self.client.post(
            reverse('storage:feedback_submit'),
            {'message': 'Фидбек без компании', 'category': 'BUG'},
        )

        self.assertEqual(response.status_code, 400)
        errors = response.json()['errors']['non_field_errors']
        self.assertIn(
            'У вас не указана компания. Обратитесь к администратору.',
            errors,
        )
        self.assertFalse(Feedback.objects.filter(author=user).exists())


class FeedbackNoCompanyFallbackTests(TestCase):
    def test_superuser_without_company_creates_stream_cad(self):
        Company.objects.all().delete()
        user = User.objects.create_superuser(
            'root_no_company',
            password='pass',
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse('storage:feedback_submit'),
            {'message': 'Фидбек без компаний', 'category': 'BUG'},
        )

        self.assertEqual(response.status_code, 200)
        company = Company.objects.get(name='Stream-CAD')
        profile = UserProfile.objects.get(user=user)
        self.assertEqual(profile.company, company)
        self.assertEqual(profile.role, 'admin')
        feedback = Feedback.objects.get(author=user)
        self.assertEqual(feedback.company, company)
