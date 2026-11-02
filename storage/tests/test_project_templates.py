from django.contrib.auth import get_user_model
from django.test import TestCase

from storage.domain.models import Company
from storage.domain.services import (
    CANONICAL_ZONE_NAMES,
    create_project_from_template,
)


User = get_user_model()


class ProjectTemplateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='template_admin',
            password='pass',
        )
        self.company = Company.objects.create(name='Шаблоны')

    def test_create_project_from_template(self):
        project = create_project_from_template(
            company=self.company,
            code='T-1',
            name='Жилой дом',
            customer='Заказчик',
            gip=None,
            created_by=self.user,
            template_key='residential',
            base_code='2345-12',
        )

        self.assertEqual(project.sections.count(), 4)
        self.assertEqual(
            set(project.sections.values_list('code', flat=True)),
            {'АР', 'КР', 'ОВ', 'ВК'},
        )
        self.assertEqual(
            project.sections.get(code='АР').full_code,
            '2345-12-АР',
        )

        template_folders = project.folders.exclude(code='')
        self.assertEqual(template_folders.count(), 3)
        folder = template_folders.get(section__code='АР', code='01')
        self.assertEqual(folder.name, 'Планы')
        self.assertEqual(folder.full_code, '2345-12-АР-01')

        zones = project.folders.filter(code='')
        self.assertEqual(zones.count(), 4)

    def test_create_project_without_template(self):
        project = create_project_from_template(
            company=self.company,
            code='T-2',
            name='Без шаблона',
            customer='',
            gip=None,
            created_by=self.user,
            template_key=None,
            base_code='',
        )

        self.assertEqual(project.sections.count(), 0)
        superfolders = project.folders.filter(
            parent=None,
            section=None,
        ).exclude(name__in=CANONICAL_ZONE_NAMES)
        self.assertEqual(superfolders.count(), 3)
        self.assertEqual(
            set(superfolders.values_list('name', flat=True)),
            {
                'Исходные данные (ИРД)',
                'ПД (Проектная документация)',
                'РД (Рабочая документация)',
            },
        )
        self.assertEqual(
            set(superfolders.values_list('code', flat=True)),
            {''},
        )
        self.assertEqual(project.folders.count(), 7)
