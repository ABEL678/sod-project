"""Тесты S3 Data Lake (Этап B: mapping + export + dirty signals).

Покрытие:
- mapping: uuid5, slug, статусы, deadline-метрики, сериализация DesignTask
- export: сборка payload, манифест, manifest_changed, фасад (moto)
- сигналы: mark_design_task_dirty
"""

import json
from datetime import date, timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from storage.domain.models import (
    ProjectMembership,
    Company,
    DesignTask,
    Project,
    Section,
    SyncState,
    User,
    UserProfile,
)
from storage.domain.sync.mapping import (
    S3_SYNC_NAMESPACE,
    SCHEMA_VERSION,
    SOURCE_SYSTEM,
    TASK_STATUS_MAP,
    _json_bytes,
    build_document_fingerprint,
    compute_deadline_metrics,
    date_iso,
    full_name,
    iso_utc,
    map_task_status,
    project_slug,
    serialize_design_task,
    serialize_rfi_task,
    serialize_section,
    sha256_hex,
    uuid_for_design_task,
    uuid_for_project,
    uuid_for_rfi,
)
from storage.domain.sync.export import (
    SyncDisabledError,
    _manifest_changed,
    build_metadata,
    build_tasks_payload,
    export_project_to_s3,
    get_s3_gateway,
    reset_s3_gateway,
)


def _profile(user, role):
    UserProfile.objects.update_or_create(
        user=user, defaults={'role': role},
    )


# ─── Mapping Unit Tests ───────────────────────────────────────────────────────


class MappingUuidTests(TestCase):
    def test_uuid_deterministic(self):
        self.assertEqual(
            uuid_for_project("P-25-118"),
            uuid_for_project("P-25-118"),
        )
        self.assertNotEqual(
            uuid_for_project("P-25-118"),
            uuid_for_project("OTHER"),
        )

    def test_uuid_for_design_task(self):
        u = uuid_for_design_task("P-25-118", 42)
        self.assertIsInstance(u, str)
        self.assertEqual(len(u), 36)  # uuid с дефисами

    def test_uuid_for_rfi(self):
        u = uuid_for_rfi("P-25-118", 7)
        self.assertIsInstance(u, str)
        self.assertEqual(len(u), 36)


class MappingSlugTests(TestCase):
    def test_slug_safe(self):
        self.assertEqual(project_slug("123-45.67"), "123-45.67")

    def test_slug_cyrillic(self):
        slug = project_slug("ЖК Север")
        self.assertNotIn(" ", slug)
        self.assertIn("-u", slug)

    def test_slug_length(self):
        slug = project_slug("A" * 200)
        self.assertLessEqual(len(slug), 100)


class MappingStatusTests(TestCase):
    def test_all_statuses_mapped(self):
        expected = {
            "DRAFT": "NOT_STARTED",
            "ISSUED": "NOT_STARTED",
            "IN_PROGRESS": "IN_PROGRESS",
            "ON_HOLD": "NOT_STARTED",
            "REVIEW": "REVIEW",
            "RETURNED": "REWORK",
            "COMPLETED": "DONE",
            "ACCEPTED": "DONE",
            "CANCELLED": "CANCELLED",
        }
        for ds, ga in expected.items():
            self.assertEqual(map_task_status(ds), ga)

    def test_unknown_status_raises(self):
        with self.assertRaises(ValueError):
            map_task_status("BOGUS")

    def test_rfi_status_mapping(self):
        from storage.domain.sync.mapping import map_rfi_state_to_task_status

        for rfi_status, expected in [
            ('open', 'NOT_STARTED'),
            ('sent', 'IN_PROGRESS'),
            ('answered', 'IN_PROGRESS'),
            ('registered', 'IN_PROGRESS'),
            ('closed', 'DONE'),
            ('escalated', 'IN_PROGRESS'),
        ]:
            self.assertEqual(map_rfi_state_to_task_status(rfi_status), expected)

    def test_rfi_unknown_status_raises(self):
        from storage.domain.sync.mapping import map_rfi_state_to_task_status

        with self.assertRaises(ValueError):
            map_rfi_state_to_task_status("BOGUS")


class MappingDeadlineTests(TestCase):
    def test_no_due_date(self):
        self.assertEqual(compute_deadline_metrics(None, "IN_PROGRESS", date(2026, 11, 16)), (0, None))

    def test_done_returns_zero(self):
        self.assertEqual(
            compute_deadline_metrics(date(2026, 11, 10), "DONE", date(2026, 11, 16)),
            (0, None),
        )

    def test_overdue(self):
        overdue_days, days_until = compute_deadline_metrics(
            date(2026, 11, 10), "IN_PROGRESS", date(2026, 11, 16),
        )
        self.assertEqual(overdue_days, 6)
        self.assertEqual(days_until, -6)

    def test_not_overdue(self):
        _, days_until = compute_deadline_metrics(
            date(2026, 11, 20), "IN_PROGRESS", date(2026, 11, 16),
        )
        self.assertEqual(days_until, 4)


class MappingSerializeDesignTaskTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Test')
        self.user = User.objects.create_user('tester')
        _profile(self.user, 'developer')
        self.project = Project.objects.create(
            company=self.company, code='T-01', name='Test',
            created_by=self.user,
        )
        self.section_ar = Section.objects.create(
            project=self.project, code='AR', name='AР', order=1,
        )
        self.section_kr = Section.objects.create(
            project=self.project, code='KR', name='КР', order=2,
        )
        self.task = DesignTask.objects.create(
            project=self.project,
            title='Test task',
            description='Test desc',
            source_section=self.section_ar,
            target_section=self.section_kr,
            author=self.user,
            assignee=self.user,
            status=DesignTask.Status.IN_PROGRESS,
            priority=DesignTask.Priority.HIGH,
            due_date=date(2026, 11, 20),
        )

    def test_serialize_has_all_keys(self):
        data = serialize_design_task(self.task, today=date(2026, 11, 16))
        self.assertEqual(data['title'], 'Test task')
        self.assertEqual(data['status'], 'IN_PROGRESS')
        self.assertEqual(data['stream_cad_priority'], 'high')
        self.assertEqual(data['section_code'], 'AR')
        self.assertEqual(data['days_until_deadline'], 4)
        self.assertIsInstance(data['id'], str)
        self.assertEqual(len(data['id']), 36)

    def test_serialize_json_bytes(self):
        data = serialize_design_task(self.task)
        b = _json_bytes(data)
        parsed = json.loads(b)
        self.assertEqual(parsed['title'], 'Test task')


class MappingSerializeSectionTests(TestCase):
    def test_section(self):
        class FakeSection:
            code = 'AR'
            name = 'Архитектура'
            full_code = '01-АР'

        data = serialize_section(FakeSection())
        self.assertEqual(data['code'], 'AR')
        self.assertEqual(data['full_code'], '01-АР')

    def test_section_null_full_code(self):
        class FakeSection:
            code = 'COMMON'
            name = 'COMMON'
            full_code = ''

        data = serialize_section(FakeSection())
        self.assertIsNone(data['full_code'])


class MappingSerializeRfiTaskTests(TestCase):
    _proj_counter = 0

    def setUp(self):
        MappingSerializeRfiTaskTests._proj_counter += 1
        self.company = Company.objects.create(name='Test')
        self.user = User.objects.create_user('rfi_tester')
        _profile(self.user, 'developer')
        self.project = Project.objects.create(
            company=self.company, code=f'RFI-{self._proj_counter:02d}', name='RFI Test',
            created_by=self.user,
        )
        self.section = Section.objects.create(
            project=self.project, code='AR', name='АР', order=1,
        )
        self._rfi_num = 0

    def _rfi(self, status='open', number=None, **kw):
        from storage.domain.models import RfiTask
        if number is None:
            self._rfi_num += 1
            number = self._rfi_num
        defaults = dict(
            project=self.project, number=number,
            subject='Test RFI', reason='Need data',
            author=self.user, assignee=self.user,
            status=status,
        )
        defaults.update(kw)
        return RfiTask.objects.create(**defaults)

    def test_serialize_rfi_basic(self):
        rfi = self._rfi()
        data = serialize_rfi_task(rfi, today=date(2026, 11, 16))
        self.assertEqual(data['title'], 'Test RFI')
        self.assertEqual(data['description'], 'Need data')
        self.assertEqual(data['is_rfi'], True)
        self.assertEqual(data['rfi_state'], 'open')
        self.assertEqual(data['status'], 'NOT_STARTED')
        self.assertEqual(data['stream_cad_number'], 'RFI-01')
        self.assertIsNone(data['section_code'])

    def test_rfi_status_mapping(self):
        for rfi_status, expected_ga in [
            ('open', 'NOT_STARTED'),
            ('sent', 'IN_PROGRESS'),
            ('answered', 'IN_PROGRESS'),
            ('registered', 'IN_PROGRESS'),
            ('closed', 'DONE'),
            ('escalated', 'IN_PROGRESS'),
        ]:
            rfi = self._rfi(status=rfi_status)
            data = serialize_rfi_task(rfi)
            self.assertEqual(data['status'], expected_ga, f'{rfi_status} -> {expected_ga}')

    def test_rfi_state_passthrough(self):
        rfi = self._rfi(status='escalated')
        data = serialize_rfi_task(rfi)
        self.assertEqual(data['rfi_state'], 'escalated')

    def test_rfi_number_padding(self):
        rfi = self._rfi(number=7)
        data = serialize_rfi_task(rfi)
        self.assertEqual(data['stream_cad_number'], 'RFI-07')

    def test_rfi_number_three_digits(self):
        rfi = self._rfi(number=101)
        data = serialize_rfi_task(rfi)
        self.assertEqual(data['stream_cad_number'], 'RFI-101')

    def test_rfi_fingerprint_null(self):
        """Без документов — request/response = None."""
        rfi = self._rfi()
        data = serialize_rfi_task(rfi)
        self.assertIsNone(data['request_document'])
        self.assertIsNone(data['response_document'])

    def test_rfi_fingerprint_request_only(self):
        from storage.domain.models import Document
        doc = Document.objects.create(
            project=self.project, section=self.section,
            code='RFI-01-Запрос', name='Запрос ТУ',
            created_by=self.user,
        )
        rfi = self._rfi()
        rfi.document_request = doc
        rfi.save()
        data = serialize_rfi_task(rfi, fingerprints={
            'request': build_document_fingerprint(doc),
            'response': None,
        })
        self.assertIsNotNone(data['request_document'])
        self.assertEqual(data['request_document']['code'], 'RFI-01-Запрос')
        self.assertIsNone(data['response_document'])

    def test_serialize_rfi_has_all_envelope_keys(self):
        rfi = self._rfi()
        data = serialize_rfi_task(rfi)
        for key in (
            'id', 'project_id', 'title', 'description', 'status',
            'rfi_state', 'stream_cad_number', 'created_at', 'updated_at',
            'request_document', 'response_document', 'sent_at', 'answered_at',
            'registered_at', 'closed_at', 'escalated_at',
        ):
            self.assertIn(key, data, f'Missing key: {key}')


class MappingUtilsTests(TestCase):
    def test_iso_utc_none(self):
        self.assertIsNone(iso_utc(None))

    def test_date_iso_none(self):
        self.assertIsNone(date_iso(None))

    def test_full_name_none(self):
        self.assertIsNone(full_name(None))

    def test_full_name_fallback(self):
        user = mock.Mock()
        user.get_full_name.return_value = ''
        user.username = 'testuser'
        self.assertEqual(full_name(user), 'testuser')

    def test_sha256_hex(self):
        h = sha256_hex(b'test')
        self.assertEqual(len(h), 64)


# ─── Export Unit Tests ────────────────────────────────────────────────────────


class ExportBuildTasksPayloadTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Test')
        self.user = User.objects.create_user('tester')
        _profile(self.user, 'developer')
        self.project = Project.objects.create(
            company=self.company, code='T-02', name='Test Export',
            created_by=self.user,
        )
        self.section_ar = Section.objects.create(
            project=self.project, code='AR', name='АР', order=1,
        )
        self.section_kr = Section.objects.create(
            project=self.project, code='KR', name='КР', order=2,
        )
        self.task = DesignTask.objects.create(
            project=self.project,
            title='Export task',
            description='Desc',
            source_section=self.section_ar,
            target_section=self.section_kr,
            author=self.user,
            status=DesignTask.Status.ISSUED,
        )

    def test_build_tasks_payload(self):
        payload = build_tasks_payload(self.project, 1, "2026-11-16T10:00:00Z")
        self.assertEqual(payload['schema_version'], SCHEMA_VERSION)
        self.assertEqual(payload['generation'], 1)
        self.assertEqual(payload['project_code'], 'T-02')
        self.assertEqual(len(payload['items']), 1)
        self.assertEqual(payload['items'][0]['title'], 'Export task')

    def test_build_tasks_payload_empty(self):
        self.task.delete()
        payload = build_tasks_payload(self.project, 1, "2026-11-16T10:00:00Z")
        self.assertEqual(len(payload['items']), 0)


class ExportBuildMetadataTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Test')
        self.user = User.objects.create_user('tester')
        self.project = Project.objects.create(
            company=self.company, code='M-01', name='Meta',
            created_by=self.user,
        )
        Section.objects.create(project=self.project, code='AR', name='АР', order=1)

    def test_build_metadata(self):
        tasks_bytes = b'{"items":[]}'
        rfis_bytes = b'{"items":[]}'
        meta = build_metadata(
            self.project, 1, "2026-11-16T10:00:00Z",
            tasks_bytes, rfis_bytes,
            counts={"tasks": 0, "rfis": 0},
        )
        self.assertEqual(meta['schema_version'], SCHEMA_VERSION)
        self.assertEqual(meta['project_code'], 'M-01')
        self.assertIn('tasks.json', meta['files'])
        self.assertIn('sha256', meta['files']['tasks.json'])

    def test_build_metadata_sections(self):
        tasks_bytes = b'{}'
        rfis_bytes = b'{}'
        meta = build_metadata(self.project, 1, "2026-11-16T10:00:00Z", tasks_bytes, rfis_bytes)
        self.assertEqual(len(meta['sections']), 1)


class ExportManifestChangedTests(TestCase):
    def test_changed_when_no_current(self):
        self.assertTrue(_manifest_changed({}, b'', b'', mock.Mock()))

    def test_not_changed_when_same(self):
        data = b'{"items":[]}'
        h = sha256_hex(data)
        current = {
            "files": {
                "tasks.json": {"sha256": h},
                "rfis.json": {"sha256": h},
            },
            "last_ack_generation": 0,
        }
        sync_state = mock.Mock()
        sync_state.last_ack_generation = 0
        self.assertFalse(_manifest_changed(current, data, data, sync_state))

    def test_changed_when_tasks_differ(self):
        current = {
            "files": {
                "tasks.json": {"sha256": "a" * 64},
                "rfis.json": {"sha256": sha256_hex(b'{}')},
            },
            "last_ack_generation": 0,
        }
        sync_state = mock.Mock()
        sync_state.last_ack_generation = 0
        self.assertTrue(_manifest_changed(current, b'{"items":[]}', b'{}', sync_state))


class ExportFascadeTests(TestCase):
    def test_disabled_raises(self):
        with self.settings(S3_SYNC_ENABLED=False):
            with self.assertRaises(SyncDisabledError):
                project = mock.Mock()
                export_project_to_s3(project)


# ─── Dirty Signal Tests ───────────────────────────────────────────────────────


class DirtySignalTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Test')
        self.user = User.objects.create_user('tester')
        self.project = Project.objects.create(
            company=self.company, code='DIRTY', name='Dirty',
            created_by=self.user,
        )
        self.section = Section.objects.create(
            project=self.project, code='AR', name='АР', order=1,
        )
        self.state = SyncState.objects.create(project=self.project, dirty=False)

    def test_post_save_marks_dirty(self):
        DesignTask.objects.create(
            project=self.project,
            title='New',
            source_section=self.section,
            target_section=self.section,
            author=self.user,
        )
        self.state.refresh_from_db()
        self.assertTrue(self.state.dirty)

    def test_post_delete_marks_dirty(self):
        task = DesignTask.objects.create(
            project=self.project,
            title='To delete',
            source_section=self.section,
            target_section=self.section,
            author=self.user,
        )
        # Сбросим флаг
        SyncState.objects.filter(project=self.project).update(dirty=False)
        task.delete()
        self.state.refresh_from_db()
        self.assertTrue(self.state.dirty)

    def test_rfi_post_save_marks_dirty(self):
        from storage.domain.models import RfiTask

        RfiTask.objects.create(
            project=self.project, number=1,
            subject='Test', reason='Need',
            author=self.user,
        )
        self.state.refresh_from_db()
        self.assertTrue(self.state.dirty)

    def test_rfi_post_delete_marks_dirty(self):
        from storage.domain.models import RfiTask

        rfi = RfiTask.objects.create(
            project=self.project, number=1,
            subject='Test', reason='Need',
            author=self.user,
        )
        SyncState.objects.filter(project=self.project).update(dirty=False)
        rfi.delete()
        self.state.refresh_from_db()
        self.assertTrue(self.state.dirty)


@override_settings(S3_SYNC_ENABLED=True)
class InboxTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Test')
        self.user = User.objects.create_user('gip')
        _profile(self.user, 'gip')
        self.project = Project.objects.create(
            company=self.company, code='INBOX', name='Inbox',
            created_by=self.user,
        )
        ProjectMembership.objects.update_or_create(project=self.project, user=self.user, defaults={'role_in_project': 'gip'})
        self.section = Section.objects.create(
            project=self.project, code='AR', name='AР', order=1,
        )
        self.state = SyncState.objects.create(project=self.project, enabled=True)
        from storage.domain.sync.mapping import project_slug
        self._slug = project_slug(self.project.code)

    def _command_key(self, seq, ctype, cid):
        return f"projects/{self._slug}/inbox/commands/{seq}_{ctype}_{cid}.json"

    def _make_command(self, seq=1, ctype='ack.import', cid='cmd-001', payload=None):
        return {"id": cid, "type": ctype, "payload": payload or {"generation": 1}}

    @mock.patch('storage.domain.sync.inbox.get_s3_gateway')
    def test_ack_import_processed(self, mock_gateway_cls):
        from storage.domain.sync.inbox import process_project_inbox
        gateway = mock.Mock()
        mock_gateway_cls.return_value = gateway
        gateway.list_keys.return_value = [self._command_key(1, 'ack.import', 'cmd-001')]
        gateway.get_json_object.return_value = self._make_command(1, 'ack.import', 'cmd-001', {"generation": 5})
        result = process_project_inbox(self.project)
        self.assertEqual(result, 1)
        self.state.refresh_from_db()
        self.assertEqual(self.state.last_inbox_seq, 1)
        self.assertEqual(self.state.last_ack_generation, 5)

    @mock.patch('storage.domain.sync.inbox.get_s3_gateway')
    def test_idempotent_by_command_id(self, mock_gateway_cls):
        from storage.domain.models import InboxCommand
        from storage.domain.sync.inbox import process_project_inbox
        InboxCommand.objects.create(
            project=self.project, command_id='cmd-001',
            seq=1, type='ack.import', status=InboxCommand.Status.DONE,
        )
        self.state.last_inbox_seq = 1
        self.state.save()
        gateway = mock.Mock()
        mock_gateway_cls.return_value = gateway
        gateway.list_keys.return_value = [self._command_key(1, 'ack.import', 'cmd-001')]
        gateway.get_json_object.return_value = self._make_command(1, 'ack.import', 'cmd-001')
        result = process_project_inbox(self.project)
        self.assertEqual(result, 0)

    @mock.patch('storage.domain.sync.inbox.get_s3_gateway')
    def test_rfi_escalate_success(self, mock_gateway_cls):
        from storage.domain.models import RfiTask
        from storage.domain.sync.inbox import process_project_inbox
        RfiTask.objects.create(
            project=self.project, number=1,
            subject='Test RFI', reason='Need',
            author=self.user, status='sent',
        )
        gateway = mock.Mock()
        mock_gateway_cls.return_value = gateway
        gateway.list_keys.return_value = [self._command_key(1, 'rfi.escalate', 'cmd-rfi')]
        gateway.get_json_object.return_value = self._make_command(
            1, 'rfi.escalate', 'cmd-rfi', payload={"rfi_number": 1},
        )
        result = process_project_inbox(self.project)
        self.assertEqual(result, 1)
        gateway.move_object.assert_called_once()
        self.assertIn('done/', gateway.move_object.call_args[0][1])