import importlib

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from storage.adapters.local_storage import LocalFileStorage
from storage.domain.models import (
    ChangeSetEntry,
    Company,
    Document,
    DocumentVersion,
    Project,
    Section,
    UserProfile,
)
from storage.domain.services import copy_document, upload_document


User = get_user_model()


@override_settings(AI_CHECKS_ENABLED=False)
class ChangeSetJournalTests(TestCase):
    """SPEC 2.0, волна 0, п. 8 — журнал изменений версии документа."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания журнала')
        self.user = User.objects.create_user('changeset-admin', password='pass')
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={'role': 'admin', 'company': self.company},
        )
        self.project = Project.objects.create(
            company=self.company,
            code='CHANGESET-01',
            name='Проект журнала',
            status='active',
            created_by=self.user,
        )
        self.section = Section.objects.create(
            project=self.project,
            code='АР',
            name='Архитектурные решения',
            order=1,
        )

    def _upload(self, **kwargs):
        defaults = {
            'user': self.user,
            'project': self.project,
            'section': self.section,
            'code': 'АР-01',
            'name': 'План',
            'doc_type': 'drawing',
            'file': SimpleUploadedFile('plan.pdf', b'%PDF-1.4 test'),
            'reason_text': 'Новая редакция',
        }
        defaults.update(kwargs)
        return upload_document(**defaults)

    def test_upload_creates_version_release_entry(self):
        version = self._upload(
            comment='Заменены узлы 3 и 4',
            affected_sheets='листы 3, 7–9',
        )

        entry = ChangeSetEntry.objects.get(version=version)
        self.assertEqual(entry.entry_type, 'version_release')
        self.assertEqual(entry.created_by, self.user)
        self.assertEqual(entry.affected_sheets, 'листы 3, 7–9')
        self.assertEqual(entry.version.comment, 'Заменены узлы 3 и 4')
        self.assertEqual(entry.text, '')

    def test_copy_creates_release_entry_for_copy(self):
        original = self._upload(reason_text='Первичная загрузка')

        copied = copy_document(
            self.user,
            original.document,
            folder=None,
            section=self.section,
            file_storage=LocalFileStorage(),
        )

        copied_version = copied.versions.get()
        entry = ChangeSetEntry.objects.get(version=copied_version)
        self.assertEqual(entry.entry_type, 'version_release')
        self.assertEqual(entry.created_by, self.user)
        self.assertEqual(entry.version.comment, original.comment)

    def test_backfill_creates_one_entry_per_existing_version(self):
        first = self._upload(code='АР-01')
        second = self._upload(code='АР-02')
        ChangeSetEntry.objects.all().delete()
        self.assertFalse(ChangeSetEntry.objects.exists())

        migration = importlib.import_module(
            'storage.migrations.0042_changesetentry'
        )
        migration.backfill_version_release_entries(
            django_apps, schema_editor=None,
        )

        for version in (first, second):
            entries = ChangeSetEntry.objects.filter(version=version)
            self.assertEqual(entries.count(), 1)
            entry = entries.get()
            self.assertEqual(entry.entry_type, 'version_release')
            self.assertEqual(entry.created_by_id, version.uploaded_by_id)
            self.assertEqual(entry.affected_sheets, '')

    def test_upload_form_passes_affected_sheets(self):
        self.client.force_login(self.user)
        url = reverse(
            'storage:document_upload_ajax',
            args=[self.project.pk],
        )
        response = self.client.post(
            url,
            {
                'section_id': self.section.pk,
                'code': 'АР-03',
                'name': 'План 3',
                'doc_type': 'drawing',
                'reason_type': 'other',
                'reason_text': 'Новая редакция по заданию ГИПа',
                'comment': 'Заменены узлы 3 и 4',
                'affected_sheets': 'листы 3, 7–9',
                'file': SimpleUploadedFile('plan3.pdf', b'%PDF-1.4 test'),
            },
        )

        self.assertEqual(response.status_code, 200, response.content)
        document = Document.objects.get(project=self.project, code='АР-03')
        entry = ChangeSetEntry.objects.get(version=document.current_version)
        self.assertEqual(entry.affected_sheets, 'листы 3, 7–9')

    def test_empty_affected_sheets_is_allowed(self):
        version = self._upload(affected_sheets='')

        entry = ChangeSetEntry.objects.get(version=version)
        self.assertEqual(entry.affected_sheets, '')
        self.assertEqual(
            DocumentVersion.objects.filter(pk=version.pk).count(), 1,
        )

    def test_document_detail_shows_journal(self):
        version = self._upload(
            comment='Заменены узлы 3 и 4',
            affected_sheets='листы 3, 7–9',
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('storage:document_detail', args=[version.document_id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Запись изменений')
        self.assertContains(response, 'Заменены узлы 3 и 4')
        self.assertContains(response, 'листы 3, 7–9')
        self.assertContains(response, 'Выпуск версии')
