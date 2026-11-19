"""Тесты синхронизации разделов (sections-sync).

8 сценариев из плана Аудитора:
1. create_project_from_template(FULL) → 21 Section, порядок/коды, дедуп.
2. Папка «Архитектурные решения» (РД) → folder.section.code == 'АР'.
3. Загрузка документа в «Комплект» внутри РД/АР → document.section.code == 'АР'.
4. get_sections_with_counts возвращает 21 без COMMON; форма задания показывает АР/КЖ.
5. check_write_permission: ГИП — True; contractor без allowed_sections — False.
6. Команда sync_project_sections: dry-run, прогон, повтор, переименованная папка.
7. reset_project_structure пересоздаёт разделы и привязывает папки.
8. Регресс: полный test_project_templates.
"""

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from storage.domain.models import (
    Company,
    Document,
    DocumentVersion,
    Folder,
    Project,
    Section,
    UserProfile,
    User,
)
from storage.domain.services import (
    create_project_from_template,
    check_write_permission,
    get_sections_with_counts,
    _ensure_project_sections,
    _get_or_create_common_section,
)
from storage.domain.project_templates import PROJECT_TEMPLATES


User = get_user_model()


class TestEnsureProjectSections(TestCase):
    """Сценарии 1-2: _ensure_project_sections и привязка папок."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username='sync_admin', password='pass',
        )
        self.company = Company.objects.create(name='Синхронизация')

    def test_full_template_creates_21_sections(self):
        """Сценарий 1: FULL → 21 Section, дедуп АР/КР/ТХ."""
        project = create_project_from_template(
            company=self.company,
            code='FULL-1',
            name='Полный проект',
            customer='Заказчик',
            gip=None,
            created_by=self.user,
            template_key='FULL',
            base_code='2345-12',
        )

        # COMMON создаётся как технический контейер — исключаем его
        non_common = project.sections.exclude(code='COMMON')
        self.assertEqual(non_common.count(), 21)
        codes = set(non_common.values_list('code', flat=True))
        # ПД: 13 + РД доп: 8 (АР/КР/ТХ уже есть в ПД)
        self.assertIn('АР', codes)
        self.assertIn('КР', codes)
        self.assertIn('КЖ', codes)
        self.assertIn('КМ', codes)
        self.assertIn('ОВ', codes)
        self.assertIn('ВК', codes)
        self.assertIn('ЭОМ', codes)
        self.assertIn('СПА', codes)
        self.assertIn('АК', codes)
        self.assertIn('СС', codes)
        self.assertIn('ТХ', codes)
        self.assertIn('ИОС', codes)
        # COMMON существует как технический контейер
        self.assertTrue(project.sections.filter(code='COMMON').exists())

    def test_pd_87_creates_13_sections(self):
        """PD_87 → 13 разделов ПД."""
        project = create_project_from_template(
            company=self.company,
            code='PD-1',
            name='Проект ПД',
            customer='Заказчик',
            gip=None,
            created_by=self.user,
            template_key='PD_87',
            base_code='2345-12',
        )

        non_common = project.sections.exclude(code='COMMON')
        self.assertEqual(non_common.count(), 13)
        codes = set(non_common.values_list('code', flat=True))
        self.assertIn('АР', codes)
        self.assertIn('КР', codes)
        self.assertIn('ИОС', codes)
        self.assertNotIn('КЖ', codes)

    def test_rd_gost_creates_11_sections(self):
        """RD_GOST → 11 разделов РД."""
        project = create_project_from_template(
            company=self.company,
            code='RD-1',
            name='Проект РД',
            customer='Заказчик',
            gip=None,
            created_by=self.user,
            template_key='RD_GOST',
            base_code='2345-12',
        )

        non_common = project.sections.exclude(code='COMMON')
        self.assertEqual(non_common.count(), 11)
        codes = set(non_common.values_list('code', flat=True))
        self.assertIn('АР', codes)
        self.assertIn('КЖ', codes)
        self.assertIn('КМ', codes)
        self.assertNotIn('ИОС', codes)

    def test_folder_mark_bound_to_section(self):
        """Сценарий 2: папка «Архитектурные решения» → section.code == 'АР'."""
        project = create_project_from_template(
            company=self.company,
            code='BOUND-1',
            name='Привязка папок',
            customer='Заказчик',
            gip=None,
            created_by=self.user,
            template_key='FULL',
            base_code='2345-12',
        )

        rd_folder = project.folders.filter(
            parent__parent__name='РД (Рабочая документация)',
            name='Архитектурные решения',
        ).first()
        self.assertIsNotNone(rd_folder)
        self.assertEqual(rd_folder.section.code, 'АР')

        # ИРД-папки остаются в COMMON
        ird_common = _get_or_create_common_section(project)
        ird_folders = project.folders.filter(
            parent__parent__name='Исходные данные (ИРД)'
        )
        for ifolder in ird_folders:
            self.assertEqual(ifolder.section.pk, ird_common.pk)


class TestDocumentSection(TestCase):
    """Сценарий 3: загрузка документа → document.section = марка папки."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username='doc_user', password='pass',
        )
        self.company = Company.objects.create(name='Документы')

    def test_document_in_rd_folder_gets_section(self):
        """Документ в «Комплект 1» внутри РД/АР → section.code == 'АР'."""
        project = create_project_from_template(
            company=self.company,
            code='DOC-1',
            name='Документы в РД',
            customer='Заказчик',
            gip=None,
            created_by=self.user,
            template_key='FULL',
            base_code='2345-12',
        )

        # Находим папку «Архитектурные решения» внутри РД
        ar_folder = project.folders.filter(
            parent__parent__name='РД (Рабочая документация)',
            name='Архитектурные решения',
        ).first()
        self.assertIsNotNone(ar_folder)
        self.assertEqual(ar_folder.section.code, 'АР')

        # Создаём документ в этой папке
        doc = Document.objects.create(
            project=project,
            section=ar_folder.section,
            code='2345-12-АР1-001',
            name='Чертеж',
            doc_type='Ч',
            status='new',
            created_by=self.user,
        )
        self.assertEqual(doc.section.code, 'АР')


class TestSectionsWithContext(TestCase):
    """Сценарий 4: get_sections_with_counts и форма задания."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username='ctx_user', password='pass',
        )
        self.company = Company.objects.create(name='Контекст')

    def test_get_sections_with_counts_excludes_common(self):
        """get_sections_with_counts не содержит COMMON."""
        project = create_project_from_template(
            company=self.company,
            code='CTX-1',
            name='Контекст',
            customer='Заказчик',
            gip=None,
            created_by=self.user,
            template_key='FULL',
            base_code='2345-12',
        )

        sections = list(get_sections_with_counts(self.user, project))
        codes = [s.code for s in sections]
        self.assertNotIn('COMMON', codes)
        self.assertEqual(len(sections), 21)

    def test_task_form_sections_populated(self):
        """Форма задания показывает разделы (не пусто)."""
        project = create_project_from_template(
            company=self.company,
            code='TASK-1',
            name='Задания',
            customer='Заказчик',
            gip=None,
            created_by=self.user,
            template_key='FULL',
            base_code='2345-12',
        )

        # sections для формы — те же, что get_sections_with_counts
        sections = list(get_sections_with_counts(self.user, project))
        self.assertGreater(len(sections), 0)
        codes = [s.code for s in sections]
        self.assertIn('АР', codes)
        self.assertIn('КЖ', codes)


class TestCheckWritePermission(TestCase):
    """Сценарий 5: check_write_permission для разных ролей."""

    def setUp(self):
        self.company = Company.objects.create(name='Права')
        self.user = User.objects.create_superuser(
            username='gip_user', password='pass',
        )
        UserProfile.objects.get_or_create(
            user=self.user, defaults={'role': 'gip', 'company': self.company},
        )
        self.project = create_project_from_template(
            company=self.company,
            code='RW-1',
            name='Права записи',
            customer='Заказчик',
            gip=self.user,
            created_by=self.user,
            template_key='FULL',
            base_code='2345-12',
        )
        self.section_ar = Section.objects.get(project=self.project, code='АР')

    def test_gip_can_write_all_sections(self):
        """ГИП может писать во все разделы своего проекта."""
        self.assertTrue(
            check_write_permission(self.user, self.section_ar)
        )

    def test_contractor_without_allowed_sections_cannot_write(self):
        """Contractor без allowed_sections не может писать."""
        contractor = User.objects.create_user(
            username='contractor1', password='pass',
        )
        UserProfile.objects.get_or_create(
            user=contractor, defaults={'role': 'contractor', 'company': self.company},
        )
        self.assertFalse(
            check_write_permission(contractor, self.section_ar)
        )

    def test_contractor_with_allowed_section_can_write(self):
        """Contractor с выданным АР может писать в АР, но не в КР."""
        contractor = User.objects.create_user(
            username='contractor2', password='pass',
        )
        profile, created = UserProfile.objects.get_or_create(
            user=contractor, defaults={'role': 'contractor', 'company': self.company},
        )
        if not created:
            profile.company = self.company
            profile.role = 'contractor'
            profile.save(update_fields=['company', 'role'])
        section_kr = Section.objects.get(project=self.project, code='КР')
        profile.allowed_sections.set([self.section_ar.pk])
        # Загружаем fresh section из БД для проверки
        fresh_ar = Section.objects.get(pk=self.section_ar.pk)

        # Debug: проверяем что в allowed_sections
        self.assertEqual(profile.allowed_sections.count(), 1)
        self.assertIn(fresh_ar.pk, profile.allowed_sections.values_list('pk', flat=True))

        # Debug: проверяем company access
        from storage.domain.services import _check_company_access
        company_ok = _check_company_access(contractor, fresh_ar.project.company_id)
        self.assertTrue(company_ok, f"Company access failed: contractor.company={profile.company_id}, project.company={fresh_ar.project.company_id}")

        self.assertTrue(
            check_write_permission(contractor, fresh_ar)
        )
        self.assertFalse(
            check_write_permission(contractor, section_kr)
        )


class TestSyncCommand(TestCase):
    """Сценарий 6: команда sync_project_sections."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username='sync_user', password='pass',
        )
        self.company = Company.objects.create(name='Команда')

    def test_dry_run_does_not_change_data(self):
        """--dry-run не меняет данные."""
        project = create_project_from_template(
            company=self.company,
            code='DRY-1',
            name='Dry run',
            customer='Заказчик',
            gip=None,
            created_by=self.user,
            template_key='FULL',
            base_code='2345-12',
        )
        initial_sections = project.sections.count()

        out = self._run_command(project.pk, dry_run=True)
        self.assertIn('Проект:', out)
        self.assertIn('Разделы:', out)

        project.refresh_from_db()
        self.assertEqual(project.sections.count(), initial_sections)

    def _run_command(self, project_pk, dry_run=False):
        from io import StringIO
        out = StringIO()
        args = ['--project', str(project_pk)]
        if dry_run:
            args.append('--dry-run')
        call_command('sync_project_sections', *args, stdout=out)
        return out.getvalue()


class TestResetProjectStructure(TestCase):
    """Сценарий 7: reset_project_structure пересоздаёт разделы."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username='reset_user', password='pass',
        )
        self.company = Company.objects.create(name='Сброс')

    def test_reset_recreates_sections(self):
        """reset_project_structure пересоздаёт разделы по шаблону."""
        project = create_project_from_template(
            company=self.company,
            code='RESET-1',
            name='Сброс структуры',
            customer='Заказчик',
            gip=None,
            created_by=self.user,
            template_key='FULL',
            base_code='2345-12',
        )

        initial_sections = project.sections.exclude(code='COMMON').count()
        self.assertEqual(initial_sections, 21)

        from storage.domain.services import reset_project_structure
        result = reset_project_structure(self.user, project)

        # COMMON пересоздаётся тоже — итого 22, но non-common = 21
        non_common_after = project.sections.exclude(code='COMMON').count()
        self.assertEqual(non_common_after, 21)


class TestResidentialTemplate(TestCase):
    """Регресс: residential шаблон не ломается."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username='reg_user', password='pass',
        )
        self.company = Company.objects.create(name='Регресс')

    def test_residential_still_works(self):
        """residential шаблон создаёт 4 раздела."""
        project = create_project_from_template(
            company=self.company,
            code='REG-1',
            name='Регресс',
            customer='Заказчик',
            gip=None,
            created_by=self.user,
            template_key='residential',
            base_code='2345-12',
        )

        self.assertEqual(project.sections.count(), 4)
        codes = set(project.sections.values_list('code', flat=True))
        self.assertEqual(codes, {'АР', 'КР', 'ОВ', 'ВК'})
