from django.contrib.auth import get_user_model
from django.test import TestCase

from storage.domain.models import Company, Folder, Project, Section
from storage.domain.services import (
    create_project,
    deploy_preset_to_project,
)


User = get_user_model()


class DeployPresetToProjectTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='deploy_admin',
            password='pass',
        )
        self.company = Company.objects.create(name='Deploy')

    def _make_project(self, code, object_type='residential'):
        return create_project(
            company=self.company,
            code=code,
            name='Deploy project',
            customer='',
            created_by=self.user,
            base_code='2345-12',
            stage=Project.STAGE_P,
            object_type=object_type,
        )

    def _snapshot(self, project):
        folders = sorted(
            Folder.objects.filter(project=project).values_list(
                'pk', 'name', 'code', 'parent_id',
            )
        )
        sections = sorted(
            Section.objects.filter(project=project).values_list(
                'pk', 'code', 'name', 'full_code',
            )
        )
        return folders, sections

    def test_full_scope_creates_sections_and_folders(self):
        """full: разделы ПД и РД созданы, список созданного не пуст."""
        project = self._make_project('DEP-FULL')
        created = deploy_preset_to_project(self.user, project, 'full')

        types = {item['type'] for item in created}
        self.assertEqual(types, {'section', 'folder'})
        self.assertGreater(
            Section.objects.filter(project=project).exclude(code='COMMON').count(),
            0,
        )
        self.assertEqual(
            sum(1 for item in created if item['type'] == 'section'),
            Section.objects.filter(project=project).count(),
        )

    def test_pd_scope_creates_only_pd_roots(self):
        """pd: созданы только разделы ПД; папка РД не появляется."""
        project = self._make_project('DEP-PD')
        deploy_preset_to_project(self.user, project, 'pd')

        self.assertFalse(
            Folder.objects.filter(
                project=project, parent__name='РД (Рабочая документация)',
            ).exists()
        )
        self.assertTrue(
            Folder.objects.filter(
                project=project, parent__name='ПД (Проектная документация)',
            ).exists()
        )

    def test_rd_scope_creates_only_rd_folders(self):
        """rd: папки РД созданы, ПД-марки не созданы."""
        project = self._make_project('DEP-RD')
        deploy_preset_to_project(self.user, project, 'rd')

        self.assertTrue(
            Folder.objects.filter(
                project=project, parent__name='РД (Рабочая документация)',
            ).exists()
        )
        self.assertFalse(
            Folder.objects.filter(
                project=project, parent__name='ПД (Проектная документация)',
            ).exists()
        )

    def test_repeat_call_is_idempotent(self):
        """Повторный вызов full не дублирует ни папки, ни раздел."""
        project = self._make_project('DEP-IDEM')
        deploy_preset_to_project(self.user, project, 'full')
        folders_before, sections_before = self._snapshot(project)

        created_again = deploy_preset_to_project(self.user, project, 'full')

        self.assertEqual(created_again, [])
        folders_after, sections_after = self._snapshot(project)
        self.assertEqual(folders_before, folders_after)
        self.assertEqual(sections_before, sections_after)

    def test_repeat_call_does_not_rename_existing_folder(self):
        """Существующая папка с тем же parent и именем не переименовывается."""
        project = self._make_project('DEP-NAME')
        deploy_preset_to_project(self.user, project, 'pd')
        folder = Folder.objects.filter(
            project=project, code='', parent__name='ПД (Проектная документация)',
        ).exclude(name='').first()
        self.assertIsNotNone(folder)
        original_name = folder.name

        deploy_preset_to_project(self.user, project, 'pd')

        folder.refresh_from_db()
        self.assertEqual(folder.name, original_name)

    def test_existing_section_full_code_is_not_overwritten(self):
        """Раздел с уже заданным full_code не перезаписывается при повторном развёртывании."""
        project = self._make_project('DEP-CODE')
        deploy_preset_to_project(self.user, project, 'pd')
        section = Section.objects.get(project=project, code='АР')
        section.full_code = 'CUSTOM-АР'
        section.save(update_fields=['full_code'])

        deploy_preset_to_project(self.user, project, 'pd')

        section.refresh_from_db()
        self.assertEqual(section.full_code, 'CUSTOM-АР')

    def test_missing_object_type_raises(self):
        """Проект без object_type: сервис отказывает понятной ошибкой."""
        project = create_project(
            company=self.company,
            code='DEP-NOTYPE',
            name='No type',
            customer='',
            created_by=self.user,
            base_code='2345-12',
        )
        with self.assertRaises(ValueError):
            deploy_preset_to_project(self.user, project, 'full')

    def test_unknown_scope_raises(self):
        """Неизвестный deploy_scope отклоняется."""
        project = self._make_project('DEP-SCOPE')
        with self.assertRaises(ValueError):
            deploy_preset_to_project(self.user, project, 'bogus')
