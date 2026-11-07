"""Общие приспособления тестов ai_checks: изоляция MEDIA_ROOT и шрифт."""

import os
import shutil
import tempfile

from django.test import override_settings

# Шрифт с кириллицей для генерации PDF в тестах (base14 Helvetica её
# не поддерживает: «Мелкий текст» превращается в мусор). Берём первый
# доступный; если нет — тесты, которым нужен текст, скипаются.
CYRILLIC_FONT_PATHS = (
    r'C:\Windows\Fonts\arial.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
)


def find_cyrillic_font():
    for path in CYRILLIC_FONT_PATHS:
        if os.path.exists(path):
            return path
    return None


class MediaIsolationMixin:
    """Уводит MEDIA_ROOT во временный каталог (тесты не сорят в media/)."""

    @classmethod
    def setUpClass(cls):
        cls._media_root = tempfile.mkdtemp(prefix='ai_checks_media_')
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
