from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from storage.domain.models import (
    ChangeSetEntry,
    Company,
    Section,
    UserProfile,
)
from storage.domain.services import (
    create_issue,
    create_project,
    transition_document_zone,
    update_internal_status,
    upload_document,
)


User = get_user_model()


@override_settings(AI_CHECKS_ENABLED=False)
class ApprovalRecordTests(TestCase):
    """SPEC 2.0, волна 0, п. 7 — запись об одобрении в журнале."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания одобрения')
        self.admin = User.objects.create_user('approval-admin', password='pass')
        UserProfile.objects.update_or_create(
            user=self.admin,
            defaults={'role': 'admin', 'company': self.company},
        )
        self.gip = User.objects.create_user('approval-gip', password='pass')
        UserProfile.objects.update_or_create(
            user=self.gip,
            defaults={'role': 'gip', 'company': self.company},
        )
        self.project = create_project(
            company=self.company,
            code='APPROVAL-01',
            name='Проект одобрения',
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

    def _publishable(self, code):
        version = upload_document(
            user=self.gip,
            project=self.project,
            section=self.section,
            code=code,
            name='План',
            doc_type='drawing',
            file=SimpleUploadedFile(f'{code}.pdf', b'%PDF-1.4 test'),
            reason_text='Новая редакция',
        )
        document = version.document
        update_internal_status(self.gip, document, 'send_to_review')
        update_internal_status(self.gip, document, 'approve_internal')
        document.refresh_from_db()
        return document

    def _approval_entry(self, document):
        return ChangeSetEntry.objects.get(
            version=document.current_version,
            entry_type='approval',
        )

    def test_approval_record_for_a(self):
        document = self._publishable('АР-A')
        document.review_result = 'S4'
        document.save(update_fields=['review_result'])
        transition_document_zone(self.gip, document, 'submit_review')
        transition_document_zone(self.gip, document, 'publish')
        document.refresh_from_db()

        entry = self._approval_entry(document)
        self.assertEqual(entry.created_by, self.gip)
        self.assertIn('Итог: A', entry.text)
        self.assertIn('review_result: S4', entry.text)
        self.assertIn('Одобрил:', entry.text)

    def test_approval_record_for_b_lists_open_issues(self):
        document = self._publishable('АР-B')
        issue = create_issue(
            user=self.gip,
            document=document,
            description='Незначительное замечание к штампу',
            is_blocking=False,
        )
        document.review_result = 'S2'
        document.save(update_fields=['review_result'])
        transition_document_zone(self.gip, document, 'submit_review')
        transition_document_zone(self.gip, document, 'publish')
        document.refresh_from_db()

        entry = self._approval_entry(document)
        self.assertIn('Итог: B', entry.text)
        self.assertIn('review_result: S2', entry.text)
        self.assertIn(f'#{issue.pk}', entry.text)
        self.assertIn('Незначительное замечание к штампу', entry.text)

    def test_no_approval_record_when_c_blocks(self):
        document = self._publishable('АР-C')
        create_issue(
            user=self.gip,
            document=document,
            description='Неблокирующее',
            is_blocking=False,
        )
        transition_document_zone(self.gip, document, 'submit_review')
        create_issue(
            user=self.gip,
            document=document,
            description='Новое блокирующее',
            is_blocking=True,
        )
        document.refresh_from_db()

        with self.assertRaises(ValidationError):
            transition_document_zone(self.gip, document, 'publish')

        self.assertFalse(
            ChangeSetEntry.objects.filter(
                version=document.current_version,
                entry_type='approval',
            ).exists()
        )
