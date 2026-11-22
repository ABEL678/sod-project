from django.contrib.auth import get_user_model
from django.test import TestCase

from storage.domain.models import Company, Document, Folder, Project, Section
from storage.domain.services import _generate_document_code, create_project


User = get_user_model()


class DocumentCodeGeneratorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='code_admin',
            password='pass',
        )
        self.company = Company.objects.create(name='Code')
        self.project = create_project(
            company=self.company,
            code='CODE-1',
            name='Code project',
            customer_id=None,
            created_by=self.user,
            base_code='TEST-2',
            stage=Project.STAGE_P,
            object_type='industrial',
        )
        self.section = Section.objects.create(
            project=self.project, code='АК', name='Автоматизация',
            full_code='TEST-2-АК', order=1,
        )

    def _folder(self, name, code, parent=None):
        return Folder.objects.create(
            project=self.project,
            section=self.section,
            parent=parent,
            name=name,
            code=code,
            full_code=code,
            zone='WIP',
            created_by=self.user,
        )

    def _assert_single_base(self, code):
        self.assertEqual(code.count('TEST-2'), 1, msg=code)

    def test_rd_kit_code_single_base(self):
        """РД-комплект {base}-{марка}{N}: шифр документа без повтора базы."""
        kit = self._folder('Комплект АК1', 'TEST-2-АК1')
        code = _generate_document_code(self.project, self.section, kit)
        self.assertEqual(code, 'TEST-2-АК1-01')
        self._assert_single_base(code)

    def test_pd_volume_code_single_base(self):
        """ПД-том {base}-{шифр}{K}: шифр документа без повтора базы."""
        vol = self._folder('Часть 1. Тест', 'TEST-2-АК1')
        code = _generate_document_code(self.project, self.section, vol)
        self.assertEqual(code, 'TEST-2-АК1-01')
        self._assert_single_base(code)

    def test_ios_volume_code_single_base(self):
        """ИОС-том {base}-ИОС{N}.{K}: шифр документа без повтора базы."""
        vol = self._folder('Часть 1. Система водоотведения', 'TEST-2-ИОС3.1')
        code = _generate_document_code(self.project, self.section, vol)
        self.assertEqual(code, 'TEST-2-ИОС3.1-01')
        self._assert_single_base(code)

    def test_parent_without_code_falls_back_to_base_section(self):
        """Родитель без кода: fallback {base}-{раздел}-NN, база один раз."""
        root = self._folder('Без кода', '')
        code = _generate_document_code(self.project, self.section, root)
        self.assertEqual(code, 'TEST-2-АК-01')
        self._assert_single_base(code)

    def test_no_folder_falls_back_to_base_section(self):
        """Документ без папки: {base}-{раздел}-NN."""
        code = _generate_document_code(self.project, self.section, None)
        self.assertEqual(code, 'TEST-2-АК-01')

    def test_number_increments_on_collision(self):
        """Перебор номера: при занятом шифре выдаётся следующий свободный."""
        kit = self._folder('Комплект АК1', 'TEST-2-АК1')
        for number in ('01', '02'):
            Document.objects.create(
                project=self.project,
                section=self.section,
                folder=kit,
                code=f'TEST-2-АК1-{number}',
                name='busy',
                doc_type='other',
                created_by=self.user,
            )
        code = _generate_document_code(self.project, self.section, kit)
        self.assertEqual(code, 'TEST-2-АК1-03')
        self._assert_single_base(code)
