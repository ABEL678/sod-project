import importlib

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from issues.models import Issue
from storage.adapters.local_storage import LocalFileStorage
from storage.domain.models import (
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
class VersionReasonTests(TestCase):
    """SPEC 2.0, волна 0, п. 5 — причина версии документа."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания причины')
        self.user = User.objects.create_user('reason-admin', password='pass')
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={'role': 'admin', 'company': self.company},
        )
        self.project = Project.objects.create(
            company=self.company,
            code='REASON-01',
            name='Проект причины',
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
        }
        defaults.update(kwargs)
        return upload_document(**defaults)

    def _make_issue(self, version):
        return Issue.objects.create(
            document=version.document,
            version=version,
            author=self.user,
            description='Исправить узел',
        )

    def test_issue_reason_is_saved_and_readable(self):
        first = self._upload(reason_text='Первичная загрузка')
        issue = self._make_issue(first)

        second = self._upload(
            reason_type='issue',
            reason_object=issue,
        )

        saved = DocumentVersion.objects.get(pk=second.pk)
        self.assertEqual(saved.reason_type, 'issue')
        self.assertEqual(saved.content_type.model, 'issue')
        self.assertEqual(saved.object_id, issue.pk)
        self.assertEqual(saved.reason, issue)

    def test_other_reason_without_text_raises(self):
        with self.assertRaisesMessage(ValueError, 'заполните текст причины'):
            self._upload(reason_type='other', reason_text='')

    def test_issue_reason_without_object_raises(self):
        with self.assertRaisesMessage(ValueError, 'укажите источник'):
            self._upload(reason_type='issue', reason_text='По замечанию')

    def test_backfill_sets_other_and_legacy_text(self):
        version = self._upload(reason_text='Первичная загрузка')
        DocumentVersion.objects.filter(pk=version.pk).update(
            reason_type='issue',
            reason_text='',
        )

        migration = importlib.import_module(
            'storage.migrations.0041_documentversion_version_reason'
        )
        migration.backfill_legacy_reason(django_apps, schema_editor=None)

        version.refresh_from_db()
        self.assertEqual(version.reason_type, 'other')
        self.assertEqual(
            version.reason_text,
            'Причина не фиксировалась до волны 0',
        )

    def test_copy_reason_contains_source_code(self):
        original = self._upload(reason_text='Первичная загрузка')

        copied = copy_document(
            self.user,
            original.document,
            folder=None,
            section=self.section,
            file_storage=LocalFileStorage(),
        )

        copied_version = copied.versions.get()
        self.assertEqual(copied_version.reason_type, 'other')
        self.assertEqual(
            copied_version.reason_text,
            f'Копия документа {original.document.code}',
        )
        self.assertNotEqual(copied_version.reason_text, original.reason_text)

    def test_upload_form_passes_comment_and_reason(self):
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
                'file': SimpleUploadedFile('plan3.pdf', b'%PDF-1.4 test'),
            },
        )

        self.assertEqual(response.status_code, 200, response.content)
        document = Document.objects.get(project=self.project, code='АР-03')
        version = document.current_version
        self.assertEqual(version.reason_type, 'other')
        self.assertEqual(version.reason_text, 'Новая редакция по заданию ГИПа')
        self.assertEqual(version.comment, 'Заменены узлы 3 и 4')
