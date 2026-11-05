"""Спринт 7: контракт перемещений, регрессии и защитные правки.

Покрывает находки генерального аудита 5.0 (ревизия 92f237c):
- DnD-контракт «Сменить раздел»: сброс в корень своего раздела без флага,
  кросс-разделный перенос только с allow_section_change=1;
- зона-гвардия PUBLISHED/SHARED/ARCHIVE не обходится через явный флаг;
- create_demo_project не запускается в продакшене без --force;
- скриншот обратной связи проверяется как изображение (PIL), а не
  только по content_type;
- LOGGING настроен (django.request, storage, issues);
- разметка: панель «Последние документы» восстановлена, DnD-пейлоад
  несёт исходный раздел.
"""
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from storage.domain.models import Company
from storage.domain.services import submit_feedback
from storage.tests.test_sprint5 import Sprint5TestBase
from storage.tests.test_feedback import TINY_PNG


class DemoCommandGuardTests(TestCase):
    """Демо-команда создаёт admin-аккаунты с известным паролем."""

    @override_settings(DEBUG=False)
    def test_demo_command_refuses_in_production(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('create_demo_project', verbosity=0)
        self.assertIn('--force', str(ctx.exception))
        self.assertIn('demo12345', str(ctx.exception))

    @override_settings(DEBUG=False)
    def test_demo_command_force_flag_unblocks_production(self):
        call_command('create_demo_project', force=True, verbosity=0)
        from storage.domain.models import Project
        self.assertTrue(Project.objects.filter(code='DEMO-2026').exists())

    @override_settings(DEBUG=True)
    def test_demo_command_runs_in_dev_mode(self):
        # Тестовый раннер Django принудительно ставит DEBUG=False,
        # поэтому dev-режим эмулируем явно.
        call_command('create_demo_project', verbosity=0)
        from storage.domain.models import Project
        self.assertTrue(Project.objects.filter(code='DEMO-2026').exists())


class DocumentSectionMoveViewTests(Sprint5TestBase):
    """Вьюха перемещения: зона-гвардия сильнее явного флага."""

    def test_published_document_rejected_even_with_flag(self):
        document = self.make_document(
            code='PUB-7', folder=self.published_folder, status='approved',
        )
        self.client.force_login(self.user_gip)

        response = self.client.post(
            reverse('storage:document_move', args=[document.pk]),
            {
                'section_id': self.section_kr.pk,
                'allow_section_change': '1',
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn('workflow-кнопки', response.json()['error'])
        document.refresh_from_db()
        self.assertEqual(document.section_id, self.section_ar.pk)
        self.assertEqual(document.folder_id, self.published_folder.pk)


class FeedbackScreenshotPilTests(TestCase):
    """Скриншот обратной связи: PIL-верификация байтов."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания С7')
        from django.contrib.auth import get_user_model
        from storage.domain.models import UserProfile

        user = get_user_model().objects.create_user('feedback7', password='pass')
        UserProfile.objects.update_or_create(user=user, defaults={'role': 'admin', 'company': self.company})[0]
        self.user = user

    def _submit(self, upload):
        return submit_feedback(
            self.user,
            self.company,
            'Сообщение со скриншотом',
            screenshot=upload,
        )

    def test_fake_png_with_spoofed_content_type_rejected(self):
        upload = SimpleUploadedFile(
            'screen.png',
            b'definitely not an image at all',
            content_type='image/png',
        )
        with self.assertRaises(ValidationError) as ctx:
            self._submit(upload)
        self.assertIn(
            'не является корректным изображением',
            str(ctx.exception.message_dict['screenshot']),
        )

    def test_fake_png_via_extension_only_rejected(self):
        """Даже валидный content_type не спасёт битые байты: проверяется
        содержимое, а не заголовок части multipart."""
        upload = SimpleUploadedFile(
            'screen.png', b'\x00\x01\x02\x03garbage',
            content_type='image/png',
        )
        with self.assertRaises(ValidationError):
            self._submit(upload)

    def test_valid_jpeg_content_type_saved(self):
        from PIL import Image

        buffer = BytesIO()
        Image.new('RGB', (2, 2), color=(200, 60, 40)).save(
            buffer, format='JPEG', quality=85,
        )
        upload = SimpleUploadedFile(
            'photo.jpg', buffer.getvalue(), content_type='image/jpeg',
        )
        feedback = self._submit(upload)
        self.assertTrue(feedback.screenshot.name.endswith('.jpg'))

    def test_valid_png_saved(self):
        upload = SimpleUploadedFile(
            'screen.png', TINY_PNG, content_type='image/png',
        )
        feedback = self._submit(upload)
        self.assertTrue(feedback.screenshot.name.startswith('feedback/screenshots/'))
        self.assertTrue(feedback.screenshot.storage.exists(feedback.screenshot.name))


class LoggingConfigTests(TestCase):
    """LOGGING-конфигурация присутствует и покрывает ключевые логгеры."""

    def test_logging_configured(self):
        config = settings.LOGGING
        self.assertIn('console', config['handlers'])
        self.assertIn('django.request', config['loggers'])
        self.assertEqual(
            config['loggers']['django.request']['level'], 'WARNING',
        )
        self.assertIn('storage', config['loggers'])
        self.assertIn('issues', config['loggers'])

    def test_root_handler_wired(self):
        self.assertEqual(settings.LOGGING['root']['handlers'], ['console'])


class Sprint7MarkupTests(TestCase):
    """Статические проверки разметки/JS по находкам аудита 5.0."""

    def _read(self, path):
        with open(path, encoding='utf-8') as fh:
            return fh.read()

    def test_app_js_dnd_payload_carries_source_section(self):
        js = self._read('static/js/app.js')
        self.assertIn('application/x-sod-section-id', js)
        self.assertIn('application/x-sod-folder-section-id', js)
        # подтверждение при кросс-разделном переносе документа
        self.assertIn('allow_section_change', js)

    def test_recent_documents_live_in_search_empty_state(self):
        # Панель убрана со страницы проекта (шум) и показывается
        # в пустом поиске — как отправная точка.
        project_html = self._read(
            'storage/adapters/templates/storage/project_detail.html'
        )
        self.assertNotIn('id="recent-documents"', project_html)
        search_html = self._read(
            'storage/adapters/templates/storage/search.html'
        )
        self.assertIn('id="recent-documents"', search_html)
        self.assertIn('not query and recent_documents', search_html)

    def test_folder_kebab_change_section_still_present(self):
        # Кебаб папки строится в JS (контракт T68), документный — в шаблоне.
        js = self._read('static/js/app.js')
        self.assertIn('data-folder-change-section', js)
        html = self._read(
            'storage/adapters/templates/storage/project_detail.html'
        )
        self.assertIn('data-doc-action="change-section"', html)

    def test_redesign_css_has_recent_documents_block(self):
        css = self._read('static/css/redesign.css')
        self.assertIn('.recent-documents', css)
