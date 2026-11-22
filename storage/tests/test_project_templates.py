from django.contrib.auth import get_user_model
from django.test import TestCase

from storage.domain.models import Company, Project
from storage.domain.project_templates import PRESETS
from storage.domain.services import create_project_from_template


PRESET_EXPECTED_MARKS = {
    ('P', 'residential'): 13,
    ('P', 'public'): 13,
    ('P', 'industrial'): 13,
    ('P', 'linear'): 10,
    ('RD', 'residential'): 24,
    ('RD', 'public'): 25,
    ('RD', 'industrial'): 28,
    ('RD', 'linear'): 14,
}

# deploy_scope: 'pd' — П, 'rd' — РД, 'full' — П+РД (объединение кодов без дублей).
# Пересечения П и РД: АР (все типы), ТХ (общественное, производственное).
DEPLOY_SCOPE_EXPECTED_MARKS = {
    ('pd', 'residential'): 13,
    ('pd', 'public'): 13,
    ('pd', 'industrial'): 13,
    ('pd', 'linear'): 10,
    ('rd', 'residential'): 24,
    ('rd', 'public'): 25,
    ('rd', 'industrial'): 28,
    ('rd', 'linear'): 14,
    ('full', 'residential'): 36,
    ('full', 'public'): 36,
    ('full', 'industrial'): 39,
    ('full', 'linear'): 24,
}


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
            customer_id=None,
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
            customer_id=None,
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
            customer_id=None,
            gip=None,
            created_by=self.user,
            template_key='RD_GOST',
            base_code='2345-12',
        )

        non_common = project.sections.exclude(code='COMMON')
        self.assertEqual(non_common.count(), 11)
        section_codes = set(non_common.values_list('code', flat=True))
        expected_rd_codes = {
            'АР', 'КР', 'КЖ', 'КМ', 'ОВ', 'ВК', 'ЭОМ', 'СС', 'ТХ', 'СПА', 'АК',
        }
        self.assertEqual(section_codes, expected_rd_codes)

    def test_create_project_full(self):
        """Шаблон FULL: 19 разделов (ПД+РД, дедуп АР/КР/ТХ)."""
        project = create_project_from_template(
            company=self.company,
            code='T-FULL',
            name='Полный проект',
            customer_id=None,
            gip=None,
            created_by=self.user,
            template_key='FULL',
            base_code='2345-12',
        )

        non_common = project.sections.exclude(code='COMMON')
        self.assertEqual(non_common.count(), 21)
        section_codes = set(non_common.values_list('code', flat=True))
        expected_codes = {
            'ПЗ', 'ПЗУ', 'АР', 'КР', 'ИОС', 'ТХ', 'ПОС', 'ООС', 'ПБ',
            'ТБЭ', 'ОДИ', 'СМ', 'ИД',  # 13 ПД
            'КЖ', 'КМ', 'ОВ', 'ВК', 'ЭОМ', 'СС', 'СПА', 'АК',  # 8 доп РД (АР/КР/ТХ дедуп)
        }
        self.assertEqual(section_codes, expected_codes)

    def test_deploy_scope_matrix_sizes(self):
        """Матрица deploy_scope: количество марок по области и типу объекта."""
        for (scope, obj), expected in DEPLOY_SCOPE_EXPECTED_MARKS.items():
            stages = {'pd': ['P'], 'rd': ['RD'], 'full': ['P', 'RD']}[scope]
            marks = set()
            for stage in stages:
                marks |= {s['mark'] for s in PRESETS[(stage, obj)]}
            self.assertEqual(len(marks), expected, msg=f'{scope}/{obj}')

    def test_deploy_scope_creates_sections_by_scope(self):
        """Проект с deploy_scope создаёт разделы по области и папки по корням ПД/РД."""
        cases = (
            ('pd', 'residential', 13),
            ('rd', 'residential', 24),
            ('full', 'residential', 36),
        )
        for index, (scope, obj, expected) in enumerate(cases):
            project = create_project_from_template(
                company=self.company,
                code=f'SCOPE-{index}',
                name='Scope',
                customer_id=None,
                gip=None,
                created_by=self.user,
                template_key=None,
                base_code='2345-12',
                object_type=obj,
                deploy_scope=scope,
            )
            codes = set(project.sections.exclude(code='COMMON').values_list('code', flat=True))
            self.assertEqual(len(codes), expected, msg=scope)

    def test_presets_mark_counts_by_stage_and_object_type(self):
        """Константы пресетов: количество марок по каждой паре (стадия, тип)."""
        self.assertEqual(set(PRESETS), set(PRESET_EXPECTED_MARKS))
        for key, expected in PRESET_EXPECTED_MARKS.items():
            self.assertEqual(len(PRESETS[key]), expected, msg=str(key))

    def test_linear_pd_preset_is_ten_sections(self):
        """П-линейный: 10 разделов п. 3(2) ПП №87, без ИС как отдельного раздела."""
        marks = [s['mark'] for s in PRESETS[('P', 'linear')]]
        self.assertEqual(
            marks,
            ['ПЗ', 'ПО', 'ТКР', 'ЗС', 'ПОС', 'ООС', 'ПБ', 'ТБЭ', 'СМ', 'ИД'],
        )

    def test_create_project_without_template(self):
        """Без шаблона: 0 разделов, 3 суперпапки."""
        project = create_project_from_template(
            company=self.company,
            code='T-2',
            name='Без шаблона',
            customer_id=None,
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
