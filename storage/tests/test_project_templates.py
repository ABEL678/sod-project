from django.contrib.auth import get_user_model
from django.test import TestCase

from storage.domain.models import Company
from storage.domain.services import create_project_from_template


User = get_user_model()


class ProjectTemplateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='template_admin',
            password='pass',
        )
        self.company = Company.objects.create(name='Шаблоны')

    def test_create_project_from_template_residential(self):
        """Легаси-шаблон: 4 раздела, папки привязаны к разделам."""
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

    def test_create_project_pd_87(self):
        """Шаблон PD_87: 13 разделов ПД, папки привязаны к разделам."""
        project = create_project_from_template(
            company=self.company,
            code='T-PD',
            name='Проект ПД',
            customer='Заказчик',
            gip=None,
            created_by=self.user,
            template_key='PD_87',
            base_code='2345-12',
        )

        non_common = project.sections.exclude(code='COMMON')
        self.assertEqual(non_common.count(), 13)
        section_codes = set(non_common.values_list('code', flat=True))
        expected_pd_codes = {
            'ПЗ', 'ПЗУ', 'АР', 'КР', 'ИОС', 'ТХ', 'ПОС', 'ООС', 'ПБ',
            'ТБЭ', 'ОДИ', 'СМ', 'ИД',
        }
        self.assertEqual(section_codes, expected_pd_codes)
        self.assertEqual(
            project.sections.get(code='АР').full_code,
            '2345-12-АР',
        )

        # Папки марок привязаны к своим разделам
        pd_folders = project.folders.filter(
            parent__parent__name='ПД (Проектная документация)'
        )
        self.assertGreater(pd_folders.count(), 0)
        ar_folder = pd_folders.filter(
            name='Раздел 3. Объемно-планировочные и архитектурные решения'
        ).first()
        if ar_folder:
            self.assertEqual(ar_folder.section.code, 'АР')

    def test_create_project_rd_gost(self):
        """Шаблон RD_GOST: 9 разделов РД, папки привязаны к разделам."""
        project = create_project_from_template(
            company=self.company,
            code='T-RD',
            name='Проект РД',
            customer='Заказчик',
            gip=None,
            created_by=self.user,
            template_key='RD_GOST',
            base_code='2345-12',
        )

        non_common = project.sections.exclude(code='COMMON')
        self.assertEqual(non_common.count(), 9)
        section_codes = set(non_common.values_list('code', flat=True))
        expected_rd_codes = {
            'АР', 'КР', 'КЖ', 'КМ', 'ОВ', 'ВК', 'ЭО', 'СС', 'ТХ',
        }
        self.assertEqual(section_codes, expected_rd_codes)

    def test_create_project_full(self):
        """Шаблон FULL: 19 разделов (ПД+РД, дедуп АР/КР/ТХ)."""
        project = create_project_from_template(
            company=self.company,
            code='T-FULL',
            name='Полный проект',
            customer='Заказчик',
            gip=None,
            created_by=self.user,
            template_key='FULL',
            base_code='2345-12',
        )

        non_common = project.sections.exclude(code='COMMON')
        self.assertEqual(non_common.count(), 19)
        section_codes = set(non_common.values_list('code', flat=True))
        expected_codes = {
            'ПЗ', 'ПЗУ', 'АР', 'КР', 'ИОС', 'ТХ', 'ПОС', 'ООС', 'ПБ',
            'ТБЭ', 'ОДИ', 'СМ', 'ИД',  # 13 ПД
            'КЖ', 'КМ', 'ОВ', 'ВК', 'ЭО', 'СС',  # 6 доп РД (АР/КР/ТХ дедуп)
        }
        self.assertEqual(section_codes, expected_codes)

    def test_create_project_without_template(self):
        """Без шаблона: 0 разделов, 3 суперпапки."""
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
            is_system_zone=False,
        )
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
