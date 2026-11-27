"""Коррекции по отчёту MSPEC-AUDIT-3 (SPEC 2.0, волна 0).

Покрывает:
- R4-1: запись одобрения при «рождении» документа сразу в утверждённом
  состоянии (upload_document);
- R3-1: fallback-счётчик открытых замечаний в таблице — каноническое
  множество ACTIVE_ISSUE_STATUSES (DISCUSSION учитывается);
- R1-2: повторный вызов бэкфила 0042 не создаёт дублей;
- R5-1: причины версии task/rfi и отказ стража Document.save();
- решение 6: вердикт фиксируется в записи одобрения и очищается при
  входе в approved/as_built (технически, без записи журнала).
"""
import importlib

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from issues.models import Issue
from storage.adapters.tables import DocumentTable
from storage.domain.models import (
    ChangeSetEntry,
    Company,
    DesignTask,
    Document,
    DocumentVersion,
    Folder,
    RfiTask,
    Section,
    UserProfile,
)
from storage.domain.services import (
    _apply_state,
    _open_issues_count,
    create_issue,
    create_project,
    get_project_documents_queryset,
    upload_document,
)


User = get_user_model()


@override_settings(AI_CHECKS_ENABLED=False)
class Audit3TestBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Компания аудита 3')
        self.admin = User.objects.create_user('audit3-admin', password='pass')
        UserProfile.objects.update_or_create(
            user=self.admin,
            defaults={'role': 'admin', 'company': self.company},
        )
        self.developer = User.objects.create_user(
            'audit3-developer', password='pass',
        )
        UserProfile.objects.update_or_create(
            user=self.developer,
            defaults={'role': 'developer', 'company': self.company},
        )
        self.gip = User.objects.create_user('audit3-gip', password='pass')
        UserProfile.objects.update_or_create(
            user=self.gip,
            defaults={'role': 'gip', 'company': self.company},
        )
        self.project = create_project(
            company=self.company,
            code='AUDIT3-01',
            name='Проект аудита 3',
            customer_id=None,
            gip=self.gip,
            created_by=self.admin,
        )
        self.section = Section.objects.create(
            project=self.project,
            code='АР',
            name='Архитектурные решения',
            order=1,
        )
        self.published_folder = Folder.objects.get(
            project=self.project, zone='PUBLISHED',
        )

    def _upload(self, code='АР-01', user=None, folder=None, **kwargs):
        return upload_document(
            user=user or self.admin,
            project=self.project,
            section=self.section,
            code=code,
            name='План',
            doc_type='drawing',
            file=SimpleUploadedFile(f'{code}.pdf', b'%PDF-1.4 test'),
            folder=folder,
            reason_text='Новая редакция',
            **kwargs,
        )

    def _approval_entries(self, document):
        return ChangeSetEntry.objects.filter(
            version=document.current_version,
            entry_type='approval',
        )


class BirthInPublishedTests(Audit3TestBase):
    """R4-1: рождение в утверждённом состоянии — запись одобрения."""

    def test_new_document_in_published_gets_approval_record(self):
        version = self._upload(
            'АР-PUB-NEW', folder=self.published_folder,
        )
        document = Document.objects.get(pk=version.document_id)

        self.assertEqual(document.state, Document.STATE_APPROVED)
        entries = self._approval_entries(document)
        self.assertEqual(entries.count(), 1)
        entry = entries.get()
        self.assertEqual(entry.created_by, self.admin)
        self.assertIn('Итог: A', entry.text)
        self.assertIn('review_result: —', entry.text)

    def test_repeat_upload_to_existing_published_keeps_behavior(self):
        self._upload('АР-PUB-REPEAT', folder=self.published_folder)
        first_document = Document.objects.get(
            project=self.project, code='АР-PUB-REPEAT',
        )
        self.assertEqual(self._approval_entries(first_document).count(), 1)

        second = self._upload(
            'АР-PUB-REPEAT', folder=self.published_folder,
        )
        second_document = second.document
        self.assertEqual(
            ChangeSetEntry.objects.filter(
                version=second, entry_type='approval',
            ).count(),
            0,
        )
        self.assertEqual(
            ChangeSetEntry.objects.filter(
                version=second_document.current_version,
                entry_type='approval',
            ).count(),
            0,
        )
        self.assertEqual(
            ChangeSetEntry.objects.filter(
                version__document=second_document,
                entry_type='approval',
            ).count(),
            1,
        )


class IssuesFallbackTests(Audit3TestBase):
    """R3-1: fallback-счётчик совпадает с аннотацией (DISCUSSION открыт)."""

    def test_discussion_counts_in_fallback_like_annotation(self):
        document = self._upload('АР-ISS').document
        issue = create_issue(
            user=self.gip,
            document=document,
            description='Вопрос в обсуждении',
            is_blocking=False,
        )
        Issue.objects.filter(pk=issue.pk).update(status='DISCUSSION')

        annotated = get_project_documents_queryset(
            self.admin, self.project, {'filters': {}},
        ).get(pk=document.pk)
        self.assertEqual(annotated.open_issues_count, 1)

        document.refresh_from_db()
        self.assertEqual(_open_issues_count(document), 1)

        table = DocumentTable([])
        fallback_html = table.render_issues(document)
        annotated_html = table.render_issues(annotated)
        self.assertIn('Открытых: 1 из 1', fallback_html)
        self.assertEqual(fallback_html, annotated_html)


class BackfillIdempotencyTests(Audit3TestBase):
    """R1-2: повторный вызов бэкфила 0042 не создаёт дублей."""

    def test_second_backfill_skips_existing_entries(self):
        first = self._upload('АР-BF-1')
        second = self._upload('АР-BF-2')
        ChangeSetEntry.objects.all().delete()

        migration = importlib.import_module(
            'storage.migrations.0042_changesetentry'
        )
        migration.backfill_version_release_entries(
            django_apps, schema_editor=None,
        )
        after_first = ChangeSetEntry.objects.count()
        self.assertEqual(after_first, 2)

        migration.backfill_version_release_entries(
            django_apps, schema_editor=None,
        )
        self.assertEqual(ChangeSetEntry.objects.count(), after_first)
        for version in (first, second):
            self.assertEqual(
                ChangeSetEntry.objects.filter(
                    version=version, entry_type='version_release',
                ).count(),
                1,
            )


class VersionReasonSourceTests(Audit3TestBase):
    """R5-1: причины версии типа task и rfi."""

    def _task(self):
        return DesignTask.objects.create(
            project=self.project,
            title='Задание',
            description='Описание',
            source_section=self.section,
            target_section=self.section,
            author=self.gip,
        )

    def _rfi(self):
        return RfiTask.objects.create(
            project=self.project,
            number=1,
            subject='Запрос',
            reason='Нужны данные',
            author=self.gip,
        )

    def test_task_reason_is_saved_and_readable(self):
        task = self._task()
        version = self._upload(
            'АР-TASK', reason_type='task', reason_object=task,
        )

        saved = DocumentVersion.objects.get(pk=version.pk)
        self.assertEqual(saved.reason_type, 'task')
        self.assertEqual(saved.content_type.model, 'designtask')
        self.assertEqual(saved.object_id, task.pk)
        self.assertEqual(saved.reason, task)

    def test_rfi_reason_is_saved_and_readable(self):
        rfi = self._rfi()
        version = self._upload(
            'АР-RFI', reason_type='rfi', reason_object=rfi,
        )

        saved = DocumentVersion.objects.get(pk=version.pk)
        self.assertEqual(saved.reason_type, 'rfi')
        self.assertEqual(saved.content_type.model, 'rfitask')
        self.assertEqual(saved.object_id, rfi.pk)
        self.assertEqual(saved.reason, rfi)

    def test_mismatched_source_object_is_rejected(self):
        task = self._task()
        with self.assertRaisesMessage(
            ValueError, 'не соответствует выбранному типу',
        ):
            self._upload('АР-TASK-BAD', reason_type='rfi', reason_object=task)
        self.assertEqual(
            DocumentVersion.objects.filter(
                document__code='АР-TASK-BAD',
            ).count(),
            0,
        )


class SaveGuardTests(Audit3TestBase):
    """R5-1: отказ стража при несанкционированной прямой записи."""

    def _document_by(self, created_by):
        return Document.objects.create(
            project=self.project,
            section=self.section,
            code='АР-GUARD',
            name='План',
            doc_type='drawing',
            created_by=created_by,
        )

    def test_direct_state_write_raises(self):
        document = self._document_by(self.developer)

        document.state = Document.STATE_APPROVED
        with self.assertRaises(ValidationError):
            document.save(update_fields=['state'])

        document.refresh_from_db()
        self.assertEqual(document.state, Document.STATE_WIP_DRAFT)

    def test_direct_state_and_folder_write_raises(self):
        document = self._document_by(self.developer)

        document.state = Document.STATE_SHARED
        document.folder = self.published_folder
        with self.assertRaises(ValidationError):
            document.save(update_fields=['state', 'folder'])

        document.refresh_from_db()
        self.assertEqual(document.state, Document.STATE_WIP_DRAFT)
        self.assertIsNone(document.folder_id)

    def test_folder_only_write_is_legal(self):
        # Осознанное поведение стража (решение владельца 25.11.2026):
        # перемещение внутри зоны без смены state не блокируется.
        document = self._document_by(self.developer)
        shared_folder = Folder.objects.get(project=self.project, zone='SHARED')

        document.folder = shared_folder
        document.save(update_fields=['folder'])

        document.refresh_from_db()
        self.assertEqual(document.folder_id, shared_folder.pk)
        self.assertEqual(document.state, Document.STATE_WIP_DRAFT)


class ApprovalClearsVerdictTests(Audit3TestBase):
    """Решение 6: вердикт фиксируется в одобрении, затем очищается."""

    def test_verdict_is_recorded_then_cleared_without_journal_entry(self):
        document = self._upload('АР-VERDICT').document
        document.review_result = 'S4'
        document.save(update_fields=['review_result'])

        _apply_state(self.admin, document, Document.STATE_APPROVED)

        document.refresh_from_db()
        self.assertEqual(document.state, Document.STATE_APPROVED)
        self.assertEqual(document.review_result, '')

        entry = self._approval_entries(document).get()
        self.assertIn('review_result: S4', entry.text)
        self.assertEqual(
            set(
                ChangeSetEntry.objects.filter(
                    version=document.current_version,
                ).values_list('entry_type', flat=True)
            ),
            {'version_release', 'approval'},
        )
