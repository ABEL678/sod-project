"""Тесты RFI-сервисов (Этап R1, раздел 14 плана).

Покрытие: создание, отправка, ответ, регистрация, эскалация, закрытие,
загрузка документов, привязка, список, сводка, автоэскалация.
"""

from datetime import date, timedelta

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from storage.domain.models import (
    Company,
    Document,
    Folder,
    Project,
    ProjectMembership,
    RfiTask,
    RfiStatusLog,
    Section,
    User,
    UserProfile,
)
from storage.domain.services import (
    attach_rfi_document,
    close_rfi,
    create_rfi,
    escalate_overdue_rfis,
    escalate_rfi,
    get_or_create_rfi_folder,
    list_rfis,
    answer_rfi,
    register_rfi_response,
    rfi_summary_for_gip,
    rfis_requiring_attention,
    send_rfi,
    upload_rfi_document,
)


def _profile(user, role, company=None):
    UserProfile.objects.update_or_create(
        user=user, defaults={'role': role, 'company': company},
    )


def _pdf(name='test.pdf', content=b'%PDF-1.4 test'):
    return SimpleUploadedFile(name, content, content_type='application/pdf')


class RfiTestBase(TestCase):
    """Фикстуры: компания, проект, разделы, пользователи."""

    def setUp(self):
        self.company = Company.objects.create(name='Компания RFI')
        self.gip = User.objects.create_user('gip_rfi', password='pass')
        _profile(self.gip, 'gip')
        self.admin = User.objects.create_user('admin_rfi', password='pass')
        _profile(self.admin, 'admin')
        self.developer = User.objects.create_user('dev_rfi', password='pass')
        _profile(self.developer, 'developer')
        self.contractor = User.objects.create_user('con_rfi', password='pass')
        _profile(self.contractor, 'contractor')
        self.outsider = User.objects.create_user('out_rfi', password='pass')
        other = Company.objects.create(name='Чужая')
        _profile(self.outsider, 'developer', company=other)

        self.project = Project.objects.create(
            company=self.company, code='RFI-TEST', name='Проект RFI',
            status='active', created_by=self.gip,
        )
        self.section = Section.objects.create(
            project=self.project, code='COMMON', name='COMMON', order=0,
        )
        for u, role in (
            (self.gip, 'gip'), (self.admin, 'developer'),
            (self.developer, 'developer'), (self.contractor, 'contractor'),
        ):
            ProjectMembership.objects.update_or_create(
                project=self.project, user=u, defaults={'role_in_project': role},
            )

    def _create_rfi(self, **kw):
        defaults = dict(subject='Нужны ТУ', reason='Нет ТУ на воду')
        defaults.update(kw)
        return create_rfi(self.developer, self.project, **defaults)

    def _upload_request(self, rfi):
        return upload_rfi_document(
            self.developer, rfi, 'request', _pdf(),
        )

    def _upload_response(self, rfi):
        return upload_rfi_document(
            self.gip, rfi, 'response', _pdf('response.pdf', b'%PDF-1.4 resp'),
        )


class CreateRfiTests(RfiTestBase):
    """create_rfi — создание, права, номер."""

    def test_developer_can_create(self):
        rfi = self._create_rfi()
        self.assertEqual(rfi.status, 'open')
        self.assertEqual(rfi.subject, 'Нужны ТУ')
        self.assertEqual(rfi.author, self.developer)
        self.assertEqual(rfi.assignee, self.gip)

    def test_contractor_can_create(self):
        rfi = create_rfi(self.contractor, self.project, 'X', 'Y')
        self.assertEqual(rfi.status, 'open')

    def test_gip_can_create(self):
        rfi = create_rfi(self.gip, self.project, 'X', 'Y')
        self.assertEqual(rfi.status, 'open')

    def test_outsider_cannot_create(self):
        with self.assertRaises(PermissionDenied):
            create_rfi(self.outsider, self.project, 'X', 'Y')

    def test_empty_subject_raises(self):
        with self.assertRaises(ValueError):
            self._create_rfi(subject='')

    def test_number_auto_increment(self):
        rfi1 = self._create_rfi()
        rfi2 = self._create_rfi(subject='Ещё ТУ')
        self.assertEqual(rfi2.number, rfi1.number + 1)

    def test_status_log_created(self):
        rfi = self._create_rfi()
        log = RfiStatusLog.objects.filter(rfi=rfi).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.old_status, '')
        self.assertEqual(log.new_status, 'open')
        self.assertEqual(log.changed_by, self.developer)


class SendRfiTests(RfiTestBase):
    """send_rfi: open → sent, требует document_request."""

    def test_send_succeeds_with_document(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        send_rfi(self.developer, rfi)
        self.assertEqual(rfi.status, 'sent')
        self.assertIsNotNone(rfi.sent_at)

    def test_send_fails_without_document(self):
        rfi = self._create_rfi()
        with self.assertRaises(ValueError):
            send_rfi(self.developer, rfi)

    def test_send_writes_log(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        send_rfi(self.developer, rfi)
        log = RfiStatusLog.objects.filter(rfi=rfi, new_status='sent').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.old_status, 'open')


class AnswerRfiTests(RfiTestBase):
    """answer_rfi: sent → answered / escalated → answered."""

    def test_answer_after_send(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        send_rfi(self.developer, rfi)
        answer_rfi(self.gip, rfi)
        rfi.refresh_from_db()
        self.assertEqual(rfi.status, 'answered')
        self.assertIsNotNone(rfi.answered_at)

    def test_answer_after_escalated(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        send_rfi(self.developer, rfi)
        escalate_rfi(self.gip, rfi)
        answer_rfi(self.gip, rfi)
        rfi.refresh_from_db()
        self.assertEqual(rfi.status, 'answered')


class RegisterRfiResponseTests(RfiTestBase):
    """register_rfi_response: answered → registered, привязка документа."""

    def test_register_with_document(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        send_rfi(self.developer, rfi)
        answer_rfi(self.gip, rfi)
        doc = self._upload_response(rfi)
        register_rfi_response(self.gip, rfi, doc)
        rfi.refresh_from_db()
        self.assertEqual(rfi.status, 'registered')
        self.assertEqual(rfi.document_response_id, doc.pk)
        self.assertIsNotNone(rfi.registered_at)

    def test_register_wrong_project_raises(self):
        rfi = self._create_rfi()
        other_project = Project.objects.create(
            company=self.company, code='OTHER', name='Другой',
            created_by=self.gip,
        )
        doc = Document.objects.create(
            project=other_project, section=self.section,
            code='X', name='X', created_by=self.gip,
        )
        with self.assertRaises(ValueError):
            register_rfi_response(self.gip, rfi, doc)


class EscalateRfiTests(RfiTestBase):
    """escalate_rfi: sent/answered → escalated."""

    def test_escalate_from_sent(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        send_rfi(self.developer, rfi)
        escalate_rfi(self.gip, rfi)
        rfi.refresh_from_db()
        self.assertEqual(rfi.status, 'escalated')
        self.assertIsNotNone(rfi.escalated_at)

    def test_escalate_from_answered(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        send_rfi(self.developer, rfi)
        answer_rfi(self.gip, rfi)
        escalate_rfi(self.gip, rfi)
        rfi.refresh_from_db()
        self.assertEqual(rfi.status, 'escalated')

    def test_author_cannot_escalate(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        send_rfi(self.developer, rfi)
        with self.assertRaises(PermissionDenied):
            escalate_rfi(self.developer, rfi)


class CloseRfiTests(RfiTestBase):
    """close_rfi: registered/escalated/open → closed."""

    def test_close_after_registered(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        send_rfi(self.developer, rfi)
        answer_rfi(self.gip, rfi)
        doc = self._upload_response(rfi)
        register_rfi_response(self.gip, rfi, doc)
        close_rfi(self.gip, rfi)
        rfi.refresh_from_db()
        self.assertEqual(rfi.status, 'closed')
        self.assertIsNotNone(rfi.closed_at)
        self.assertEqual(rfi.closed_by, self.gip)

    def test_close_draft_requires_comment(self):
        rfi = self._create_rfi()
        with self.assertRaises(ValueError):
            close_rfi(self.gip, rfi)

    def test_close_draft_with_comment_ok(self):
        rfi = self._create_rfi()
        close_rfi(self.gip, rfi, comment='Передумали')
        rfi.refresh_from_db()
        self.assertEqual(rfi.status, 'closed')

    def test_close_escalated_no_comment_ok(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        send_rfi(self.developer, rfi)
        escalate_rfi(self.gip, rfi)
        close_rfi(self.gip, rfi)
        rfi.refresh_from_db()
        self.assertEqual(rfi.status, 'closed')


class TransitionPermissionTests(RfiTestBase):
    """Проверка прав на переходы из матрицы 6.1."""

    def test_outsider_cannot_send(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            send_rfi(self.outsider, rfi)

    def test_outsider_cannot_escalate(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        send_rfi(self.developer, rfi)
        with self.assertRaises(PermissionDenied):
            escalate_rfi(self.outsider, rfi)

    def test_invalid_transition_raises(self):
        rfi = self._create_rfi()
        with self.assertRaises(ValueError):
            close_rfi(self.gip, rfi)  # open → close без комментария


class RfiFolderTests(RfiTestBase):
    """get_or_create_rfi_folder."""

    def test_creates_folder(self):
        folder = get_or_create_rfi_folder(self.project, self.gip)
        self.assertEqual(folder.name, 'Запросы информации (RFI)')
        self.assertIsNotNone(folder.pk)

    def test_get_or_create_is_idempotent(self):
        f1 = get_or_create_rfi_folder(self.project, self.gip)
        f2 = get_or_create_rfi_folder(self.project, self.gip)
        self.assertEqual(f1.pk, f2.pk)


class UploadRfiDocumentTests(RfiTestBase):
    """upload_rfi_document."""

    def test_upload_request_creates_document(self):
        rfi = self._create_rfi()
        doc = self._upload_request(rfi)
        self.assertIsNotNone(doc.pk)
        rfi.refresh_from_db()
        self.assertEqual(rfi.document_request_id, doc.pk)
        self.assertEqual(doc.code, f'RFI-{rfi.number:02d}-Запрос')

    def test_upload_response_creates_document(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        send_rfi(self.developer, rfi)
        answer_rfi(self.gip, rfi)
        doc = self._upload_response(rfi)
        rfi.refresh_from_db()
        self.assertEqual(rfi.document_response_id, doc.pk)
        self.assertEqual(doc.code, f'RFI-{rfi.number:02d}-Ответ')

    def test_outsider_cannot_upload(self):
        rfi = self._create_rfi()
        with self.assertRaises(PermissionDenied):
            upload_rfi_document(self.outsider, rfi, 'request', _pdf())

    def test_invalid_kind_raises(self):
        rfi = self._create_rfi()
        with self.assertRaises(ValueError):
            upload_rfi_document(self.gip, rfi, 'invalid', _pdf())


class AttachRfiDocumentTests(RfiTestBase):
    """attach_rfi_document."""

    def test_attach_existing_document(self):
        rfi = self._create_rfi()
        folder = get_or_create_rfi_folder(self.project, self.gip)
        doc = Document.objects.create(
            project=self.project, section=self.section,
            code='RFI-01-Ответ', name='Ответ', folder=folder,
            created_by=self.gip,
        )
        attach_rfi_document(self.gip, rfi, 'response', doc)
        rfi.refresh_from_db()
        self.assertEqual(rfi.document_response_id, doc.pk)

    def test_attach_wrong_folder_raises(self):
        rfi = self._create_rfi()
        doc = Document.objects.create(
            project=self.project, section=self.section,
            code='X', name='X', created_by=self.gip,
        )
        with self.assertRaises(ValueError):
            attach_rfi_document(self.gip, rfi, 'response', doc)


class ListRfiTests(RfiTestBase):
    """list_rfis."""

    def test_list_all(self):
        self._create_rfi()
        self._create_rfi(subject='Второй')
        qs = list_rfis(self.project, self.gip)
        self.assertEqual(qs.count(), 2)

    def test_list_filtered(self):
        self._create_rfi()
        qs = list_rfis(self.project, self.gip, status_filter=['open'])
        self.assertEqual(qs.count(), 1)
        qs = list_rfis(self.project, self.gip, status_filter=['sent'])
        self.assertEqual(qs.count(), 0)

    def test_outsider_cannot_list(self):
        with self.assertRaises(PermissionDenied):
            list_rfis(self.project, self.outsider)

    def test_contractor_can_list(self):
        rfi = self._create_rfi()
        qs = list_rfis(self.project, self.contractor)
        self.assertIn(rfi, list(qs))


class SummaryTests(RfiTestBase):
    """rfi_summary_for_gip, rfis_requiring_attention."""

    def test_summary_counts(self):
        s = rfi_summary_for_gip(self.project)
        self.assertEqual(s['total'], 0)
        self._create_rfi()
        s = rfi_summary_for_gip(self.project)
        self.assertEqual(s['total'], 1)
        self.assertEqual(s['open'], 1)

    def test_summary_overdue(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        send_rfi(self.developer, rfi)
        RfiTask.objects.filter(pk=rfi.pk).update(
            rfi_due_date=date.today() - timedelta(days=1),
        )
        s = rfi_summary_for_gip(self.project)
        self.assertEqual(s['overdue'], 1)

    def test_requiring_attention_sent_overdue(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        send_rfi(self.developer, rfi)
        RfiTask.objects.filter(pk=rfi.pk).update(
            rfi_due_date=date.today() - timedelta(days=2),
        )
        attention = rfis_requiring_attention(self.project)
        self.assertEqual(len(attention), 1)
        self.assertEqual(attention[0].attention_reason, 'overdue_sent')

    def test_requiring_attention_escalated(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        send_rfi(self.developer, rfi)
        escalate_rfi(self.gip, rfi)
        attention = rfis_requiring_attention(self.project)
        self.assertEqual(len(attention), 1)
        self.assertEqual(attention[0].attention_reason, 'escalated')


class EscalateOverdueTests(RfiTestBase):
    """escalate_overdue_rfis (автоэскалация)."""

    def test_escalates_overdue_sent(self):
        rfi = self._create_rfi()
        self._upload_request(rfi)
        rfi.refresh_from_db()
        send_rfi(self.developer, rfi)
        RfiTask.objects.filter(pk=rfi.pk).update(
            rfi_due_date=date.today() - timedelta(days=1),
        )
        count = escalate_overdue_rfis()
        self.assertEqual(count, 1)
        rfi.refresh_from_db()
        self.assertEqual(rfi.status, 'escalated')

    def test_skips_non_overdue(self):
        self._create_rfi()
        self.assertEqual(escalate_overdue_rfis(), 0)


