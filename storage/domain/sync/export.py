"""Оркестратор выгрузки заданий в S3 Data Lake (ГИП-Ассистент).

Единственное место модуля синхронизации, где разрешены ORM-запросы и вызовы S3.
"""

import json
import logging
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from storage.adapters.s3_boto3 import S3Boto3Gateway
from storage.domain.models import Project, SyncLog, SyncState
from storage.domain.sync.mapping import (
    SCHEMA_VERSION,
    SOURCE_SYSTEM,
    _json_bytes,
    project_slug,
    serialize_design_task,
    serialize_section,
    sha256_hex,
)

logger = logging.getLogger(__name__)

LOCK_TTL = 300


class SyncDisabledError(RuntimeError):
    pass


# ─── Gateway DI ────────────────────────────────────────────────────────────────

_gateway_instance = None


def get_s3_gateway():
    global _gateway_instance
    if _gateway_instance is None:
        _gateway_instance = S3Boto3Gateway()
    return _gateway_instance


def reset_s3_gateway():
    global _gateway_instance
    _gateway_instance = None


# ─── Сборка payload'ов ────────────────────────────────────────────────────────


def build_tasks_payload(project, generation: int, generated_at: str) -> dict:
    tasks = (
        project.design_tasks
        .select_related("source_section", "target_section", "author", "assignee")
        .order_by("pk")
    )
    items = [serialize_design_task(t) for t in tasks]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "generation": generation,
        "source_system": SOURCE_SYSTEM,
        "project_code": project.code,
        "items": items,
    }


def build_metadata(
    project, generation: int, generated_at: str,
    tasks_bytes: bytes, rfis_bytes: bytes,
    counts: dict | None = None,
    sync_state=None,
) -> dict:
    sections = project.sections.order_by("order")
    counts = counts or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "generation": generation,
        "source_system": SOURCE_SYSTEM,
        "project_code": project.code,
        "project_name": project.name,
        "project_slug": project_slug(project.code),
        "sections": [serialize_section(s) for s in sections],
        "files": {
            "tasks.json": {"sha256": sha256_hex(tasks_bytes), "size_bytes": len(tasks_bytes)},
            "rfis.json": {"sha256": sha256_hex(rfis_bytes), "size_bytes": len(rfis_bytes)},
        },
        "counts": {
            "tasks": counts.get("tasks", 0),
            "rfis": counts.get("rfis", 0),
        },
        "last_ack_generation": (sync_state.last_ack_generation if sync_state else 0),
    }


def _manifest_changed(current: dict, tasks_bytes: bytes, rfis_bytes: bytes, sync_state) -> bool:
    cur_files = current.get("files") or {}
    if cur_files.get("tasks.json", {}).get("sha256") != sha256_hex(tasks_bytes):
        return True
    if cur_files.get("rfis.json", {}).get("sha256") != sha256_hex(rfis_bytes):
        return True
    if current.get("last_ack_generation", 0) != sync_state.last_ack_generation:
        return True
    return False


# ─── Фасад выгрузки ────────────────────────────────────────────────────────────


def export_project_to_s3(project) -> int:
    if not settings.S3_SYNC_ENABLED:
        raise SyncDisabledError("S3_SYNC_ENABLED=False")

    prefix = f"projects/{project_slug(project.code)}/outbox"
    gateway = get_s3_gateway()
    gateway.ensure_bucket()

    lock_key = f"s3sync:{project.pk}"
    if not cache.add(lock_key, "1", timeout=LOCK_TTL):
        logger.warning("export_project_to_s3[%s]: already running, skipped", project.code)
        state = SyncState.objects.get(project=project)
        return state.generation

    try:
        sync_state, _ = SyncState.objects.get_or_create(project=project)
        new_generation = sync_state.generation + 1
        generated_at = _now_iso()

        tasks_payload = build_tasks_payload(project, new_generation, generated_at)
        tasks_bytes = _json_bytes(tasks_payload)

        rfis_payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "generation": new_generation,
            "source_system": SOURCE_SYSTEM,
            "project_code": project.code,
            "items": [],
        }
        rfis_bytes = _json_bytes(rfis_payload)

        metadata_payload = build_metadata(
            project, new_generation, generated_at, tasks_bytes, rfis_bytes,
            counts={"tasks": len(tasks_payload["items"]), "rfis": 0},
            sync_state=sync_state,
        )
        metadata_bytes = _json_bytes(metadata_payload)

        current = (
            gateway.get_json_object(f"{prefix}/metadata.json")
            if gateway.object_exists(f"{prefix}/metadata.json")
            else None
        )
        if current and not _manifest_changed(current, tasks_bytes, rfis_bytes, sync_state):
            sync_state.dirty = False
            sync_state.last_success_at = timezone.now()
            sync_state.consecutive_failures = 0
            sync_state.last_error = ""
            sync_state.save(update_fields=[
                "dirty", "last_success_at", "consecutive_failures", "last_error",
            ])
            SyncLog.objects.create(
                project=project, direction="out", level="info",
                generation=sync_state.generation,
                message="no changes, skipped",
            )
            return sync_state.generation

        gateway.put_json_object(f"{prefix}/tasks.json", tasks_bytes)
        gateway.put_json_object(f"{prefix}/rfis.json", rfis_bytes)
        gateway.put_json_object(f"{prefix}/metadata.json", metadata_bytes)

        with transaction.atomic():
            sync_state.generation = F("generation") + 1
            sync_state.dirty = False
            sync_state.last_success_at = timezone.now()
            sync_state.consecutive_failures = 0
            sync_state.last_error = ""
            sync_state.save(update_fields=[
                "generation", "dirty", "last_success_at",
                "consecutive_failures", "last_error",
            ])
            SyncLog.objects.create(
                project=project, direction="out", level="info",
                generation=new_generation,
                message="snapshot exported",
                detail={
                    "tasks": len(tasks_payload["items"]),
                    "rfis": 0,
                    "bytes": [len(tasks_bytes), len(rfis_bytes), len(metadata_bytes)],
                    "changed": True,
                },
            )
            sync_state.refresh_from_db()
        return sync_state.generation
    finally:
        cache.delete(lock_key)


def _now_iso() -> str:
    return timezone.now().astimezone(dt_timezone.utc).isoformat().replace("+00:00", "Z")