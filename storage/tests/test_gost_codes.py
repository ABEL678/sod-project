from django.contrib.auth import get_user_model
from django.test import TestCase

from storage.domain.models import Company, Project
from storage.domain.services import (
    create_folder,
    create_section,
    generate_gost_code,
)


User = get_user_model()


class GostCodeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='gost_admin',
            password='pass',
        )
        self.company = Company.objects.create(name='ГОСТ')
        self.project = Project.objects.create(
            company=self.company,
            code='P-2345',
            base_code='2345-12',
            name='Проект',
            status='active',
            gip=self.user,
            created_by=self.user,
        )

    def test_generate_gost_code_section(self):
        self.assertEqual(
            generate_gost_code('2345-12', 'АР'),
            '2345-12-АР',
        )

    def test_generate_gost_code_folder(self):
        self.assertEqual(
            generate_gost_code('2345-12', 'АР', '01'),
            '2345-12-АР-01',
        )

    def test_create_section_auto_fills_full_code(self):
        section = create_section(
            self.project,
            'АР',
            'Архитектурные решения',
            1,
            user=self.user,
        )
        self.assertEqual(section.full_code, '2345-12-АР')

    def test_create_folder_auto_fills_full_code(self):
        section = create_section(
            self.project,
            'АР',
            'Архитектурные решения',
            1,
            user=self.user,
        )
        folder = create_folder(
            self.user,
            self.project,
            'Рабочая документация',
            section=section,
            code='01',
        )
        self.assertEqual(folder.full_code, '2345-12-АР-01')
