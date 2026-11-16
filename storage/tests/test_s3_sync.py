"""Тесты S3 Data Lake (Этап B: mapping + export + dirty signals).

Покрытие:
- mapping: uuid5, slug, статусы, deadline-метрики, сериализация DesignTask
- export: сборка payload, манифест, manifest_changed, фасад (moto)
- сигналы: mark_design_task_dirty
"""

import json
from datetime import date, timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from storage.domain.models import (
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
    compute_deadline_metrics,
    date_iso,
    full_name,
    iso_utc,
    map_task_status,
    project_slug,
    serialize_design_task,
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